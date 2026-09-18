use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{info, debug, warn};

use crate::types::DatasourceConfig;

/// Unified connection pool — either MySQL/Doris (mysql_async) or PostgreSQL (deadpool-postgres).
#[derive(Clone)]
pub enum DbPool {
    MySQL(mysql_async::Pool),
    Postgres(deadpool_postgres::Pool),
}

impl std::fmt::Debug for DbPool {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DbPool::MySQL(_) => write!(f, "DbPool::MySQL"),
            DbPool::Postgres(_) => write!(f, "DbPool::Postgres"),
        }
    }
}

/// Connection pool manager — caches connection pools by datasource key.
/// Key format: "db_type:host:port:database[:ssl]"
#[derive(Clone)]
pub struct ConnectionPoolManager {
    pools: Arc<RwLock<HashMap<String, DbPool>>>,
}

impl ConnectionPoolManager {
    pub fn new() -> Self {
        Self {
            pools: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Get or create a connection pool for the given datasource config.
    pub async fn get_or_create(&self, config: &DatasourceConfig) -> DbPool {
        let key = Self::pool_key(config);

        // Fast path: read lock check
        {
            let pools = self.pools.read().await;
            if let Some(pool) = pools.get(&key) {
                return pool.clone();
            }
        }

        // Slow path: write lock, create new pool
        let mut pools = self.pools.write().await;
        // Double-check after acquiring write lock
        if let Some(pool) = pools.get(&key) {
            return pool.clone();
        }

        let pool = Self::create_pool(config);
        pools.insert(key.clone(), pool.clone());
        info!("Created connection pool: {}", key);
        pool
    }

    fn create_pool(config: &DatasourceConfig) -> DbPool {
        match config.db_type.to_lowercase().as_str() {
            "postgres" | "postgresql" | "pg" | "sls" => Self::create_postgres_pool(config),
            _ => DbPool::MySQL(Self::create_mysql_pool(config)),
        }
    }

    fn create_mysql_pool(config: &DatasourceConfig) -> mysql_async::Pool {
        let mut builder = mysql_async::OptsBuilder::default()
            .ip_or_hostname(&config.host)
            .tcp_port(config.port)
            .db_name(Some(&config.database))
            .user(Some(&config.user))
            .pass(Some(&config.password))
            // Disable Unix socket preference — Doris doesn't support @@socket
            .prefer_socket(false);

        let ssl_mode = effective_ssl_mode(config);
        if ssl_mode != "disabled" {
            // Accept any server certificate — matches Python pymysql ssl_mode="REQUIRED"
            // behavior which only enforces TLS encryption, not certificate validation.
            let ssl_opts = mysql_async::SslOpts::default()
                .with_danger_accept_invalid_certs(true);
            builder = builder.ssl_opts(Some(ssl_opts));
            info!(
                "MySQL SSL enabled for {}:{} (ssl_mode={})",
                config.host, config.port, ssl_mode
            );
        }

        let opts = mysql_async::Opts::from(builder);
        mysql_async::Pool::new(opts)
    }

    fn create_postgres_pool(config: &DatasourceConfig) -> DbPool {
        let mut pg_config = tokio_postgres::Config::new();
        pg_config
            .host(&config.host)
            .port(config.port)
            .user(&config.user)
            .password(&config.password)
            .dbname(&config.database);

        let ssl_mode = effective_ssl_mode(config);
        if ssl_mode != "disabled" {
            // PostgreSQL TLS via tokio-postgres-rustls
            match make_pg_tls() {
                Ok(tls) => {
                    info!(
                        "Postgres SSL enabled for {}:{} (ssl_mode={})",
                        config.host, config.port, ssl_mode
                    );
                    let mgr = deadpool_postgres::Manager::new(pg_config, tls);
                    let pool = deadpool_postgres::Pool::builder(mgr)
                        .max_size(8)
                        .build()
                        .expect("Failed to build Postgres TLS pool");
                    return DbPool::Postgres(pool);
                }
                Err(e) => {
                    if ssl_mode == "required" {
                        warn!(
                            "Postgres SSL required but TLS setup failed for {}:{}: {}",
                            config.host, config.port, e
                        );
                    } else {
                        warn!(
                            "Postgres TLS setup failed for {}:{}: {}; connecting without TLS",
                            config.host, config.port, e
                        );
                    }
                }
            }
        }

        let mgr = deadpool_postgres::Manager::new(pg_config, tokio_postgres::NoTls);
        let pool = deadpool_postgres::Pool::builder(mgr)
            .max_size(8)
            .build()
            .expect("Failed to build Postgres pool");
        DbPool::Postgres(pool)
    }

    fn pool_key(config: &DatasourceConfig) -> String {
        let ssl_suffix = if effective_ssl_mode(config) != "disabled" { ":ssl" } else { "" };
        format!(
            "{}:{}:{}:{}{}",
            config.db_type.to_lowercase(),
            config.host,
            config.port,
            config.database,
            ssl_suffix,
        )
    }

    /// Remove a specific pool (e.g., on connection errors)
    #[allow(dead_code)]
    pub async fn remove(&self, config: &DatasourceConfig) {
        let key = Self::pool_key(config);
        let mut pools = self.pools.write().await;
        pools.remove(&key);
        debug!("Removed connection pool: {}", key);
    }

    /// Get number of active pools
    #[allow(dead_code)]
    pub async fn active_pools(&self) -> usize {
        let pools = self.pools.read().await;
        pools.len()
    }
}

/// Resolve effective SSL mode from config's ssl_mode and legacy ssl flag.
fn effective_ssl_mode(config: &DatasourceConfig) -> &str {
    if let Some(ref mode) = config.ssl_mode {
        match mode.as_str() {
            "required" | "preferred" => return mode.as_str(),
            _ => {}
        }
    }
    if config.ssl.unwrap_or(false) {
        return "required";
    }
    "disabled"
}

/// Create a PostgreSQL TLS connector using rustls with a permissive verifier
/// that accepts any server certificate (matching MySQL SslOpts::default behavior).
fn make_pg_tls() -> Result<tokio_postgres_rustls::MakeRustlsConnect, String> {
    use rustls::ClientConfig;
    use std::sync::Arc;

    let mut config = ClientConfig::builder()
        .with_root_certificates(rustls::RootCertStore::empty())
        .with_no_client_auth();
    config
        .dangerous()
        .set_certificate_verifier(Arc::new(AcceptAllVerifier));

    Ok(tokio_postgres_rustls::MakeRustlsConnect::new(config))
}

/// A certificate verifier that accepts any server certificate.
/// This matches the MySQL SslOpts::default() behavior of negotiating TLS
/// without verifying the server's certificate chain.
#[derive(Debug)]
struct AcceptAllVerifier;

impl rustls::client::danger::ServerCertVerifier for AcceptAllVerifier {
    fn verify_server_cert(
        &self,
        _end_entity: &rustls::pki_types::CertificateDer<'_>,
        _intermediates: &[rustls::pki_types::CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp_response: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        vec![
            rustls::SignatureScheme::ECDSA_NISTP384_SHA384,
            rustls::SignatureScheme::ECDSA_NISTP256_SHA256,
            rustls::SignatureScheme::RSA_PSS_SHA512,
            rustls::SignatureScheme::RSA_PSS_SHA384,
            rustls::SignatureScheme::RSA_PSS_SHA256,
            rustls::SignatureScheme::ED25519,
        ]
    }
}
