use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{info, debug};

use crate::types::DatasourceConfig;

/// Connection pool manager — caches MySQL connection pools by datasource key.
/// Key format: "host:port:database"
#[derive(Clone)]
pub struct ConnectionPoolManager {
    pools: Arc<RwLock<HashMap<String, mysql_async::Pool>>>,
}

impl ConnectionPoolManager {
    pub fn new() -> Self {
        Self {
            pools: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Get or create a connection pool for the given datasource config.
    pub async fn get_or_create(&self, config: &DatasourceConfig) -> mysql_async::Pool {
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

        let pool = self.create_pool(config);
        pools.insert(key.clone(), pool.clone());
        info!("Created connection pool: {}", key);
        pool
    }

    fn create_pool(&self, config: &DatasourceConfig) -> mysql_async::Pool {
        let mut builder = mysql_async::OptsBuilder::default()
            .ip_or_hostname(&config.host)
            .tcp_port(config.port)
            .db_name(Some(&config.database))
            .user(Some(&config.user))
            .pass(Some(&config.password));

        if config.ssl.unwrap_or(false) {
            builder = builder.ssl_opts(Some(mysql_async::SslOpts::default()));
        }

        let opts = mysql_async::Opts::from(builder);
        mysql_async::Pool::new(opts)
    }

    fn pool_key(config: &DatasourceConfig) -> String {
        format!("{}:{}:{}", config.host, config.port, config.database)
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
