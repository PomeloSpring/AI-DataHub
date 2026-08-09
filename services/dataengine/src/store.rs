/// Datasource Store — SQLite-backed persistent storage for datasource configurations.
///
/// Stores connection details (host, port, credentials) so API callers only need
/// to pass a `datasource_id` instead of full credentials each time.

use std::sync::{Arc, Mutex};

use chrono::Utc;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use tracing::info;
use uuid::Uuid;

use crate::types::DatasourceConfig;

/// Request body for creating/updating a datasource
#[derive(Debug, Clone, Deserialize)]
pub struct CreateDatasource {
    pub name: String,
    pub db_type: Option<String>,      // default "mysql"
    pub host: String,
    pub port: Option<u16>,            // default 3306
    pub database: String,
    pub username: String,
    pub password: String,
    pub ssl: Option<bool>,            // default false
}

/// Datasource record returned to API callers (no password)
#[derive(Debug, Clone, Serialize)]
pub struct DatasourceRecord {
    pub id: String,
    pub name: String,
    pub db_type: String,
    pub host: String,
    pub port: u16,
    pub database: String,
    pub username: String,
    pub ssl: bool,
    pub created_at: String,
    pub updated_at: String,
}

/// SQLite-backed datasource store
#[derive(Clone)]
pub struct DatasourceStore {
    conn: Arc<Mutex<Connection>>,
}

impl DatasourceStore {
    /// Create a new store, initializing the SQLite database if needed.
    pub fn new(path: &str) -> Result<Self, String> {
        let conn = Connection::open(path).map_err(|e| format!("SQLite open failed: {}", e))?;

        // Enable WAL mode for better concurrent read performance
        conn.execute_batch("PRAGMA journal_mode=WAL;")
            .map_err(|e| format!("SQLite pragma failed: {}", e))?;

        conn.execute(
            "CREATE TABLE IF NOT EXISTS datasources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                db_type TEXT NOT NULL DEFAULT 'mysql',
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 3306,
                database_name TEXT NOT NULL,
                username TEXT NOT NULL,
                password_encrypted TEXT NOT NULL,
                ssl INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )",
            [],
        ).map_err(|e| format!("SQLite create table failed: {}", e))?;

        info!("Datasource store initialized at {}", path);
        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
        })
    }

    /// Create a new datasource, returns the generated ID.
    pub fn create(&self, ds: &CreateDatasource) -> Result<String, String> {
        let id = Uuid::new_v4().to_string();
        let now = Utc::now().to_rfc3339();
        let db_type = ds.db_type.as_deref().unwrap_or("mysql");
        let port = ds.port.unwrap_or(3306);
        let ssl = if ds.ssl.unwrap_or(false) { 1 } else { 0 };

        // Simple obfuscation: base64 encode the password
        let password_enc = base64_encode(&ds.password);

        let conn = self.conn.lock().map_err(|e| format!("Lock failed: {}", e))?;
        conn.execute(
            "INSERT INTO datasources (id, name, db_type, host, port, database_name, username, password_encrypted, ssl, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![id, ds.name, db_type, ds.host, port, ds.database, ds.username, password_enc, ssl, now, now],
        ).map_err(|e| format!("Insert failed: {}", e))?;

        Ok(id)
    }

    /// List all datasources (without passwords).
    pub fn list(&self) -> Result<Vec<DatasourceRecord>, String> {
        let conn = self.conn.lock().map_err(|e| format!("Lock failed: {}", e))?;
        let mut stmt = conn
            .prepare("SELECT id, name, db_type, host, port, database_name, username, ssl, created_at, updated_at FROM datasources ORDER BY created_at DESC")
            .map_err(|e| format!("Prepare failed: {}", e))?;

        let rows = stmt
            .query_map([], |row| {
                Ok(DatasourceRecord {
                    id: row.get(0)?,
                    name: row.get(1)?,
                    db_type: row.get(2)?,
                    host: row.get(3)?,
                    port: row.get::<_, u16>(4)?,
                    database: row.get(5)?,
                    username: row.get(6)?,
                    ssl: row.get::<_, i32>(7)? != 0,
                    created_at: row.get(8)?,
                    updated_at: row.get(9)?,
                })
            })
            .map_err(|e| format!("Query failed: {}", e))?;

        let mut result = Vec::new();
        for row in rows {
            result.push(row.map_err(|e| format!("Row read failed: {}", e))?);
        }
        Ok(result)
    }

    /// Get a single datasource by ID (includes decrypted password, for internal use).
    pub fn get(&self, id: &str) -> Result<DatasourceConfig, String> {
        let conn = self.conn.lock().map_err(|e| format!("Lock failed: {}", e))?;
        let mut stmt = conn
            .prepare("SELECT db_type, host, port, database_name, username, password_encrypted, ssl FROM datasources WHERE id = ?1")
            .map_err(|e| format!("Prepare failed: {}", e))?;

        let result = stmt.query_row(params![id], |row| {
            let password_enc: String = row.get(5)?;
            Ok(DatasourceConfig {
                db_type: row.get(0)?,
                host: row.get(1)?,
                port: row.get(2)?,
                database: row.get(3)?,
                user: row.get(4)?,
                password: base64_decode(&password_enc),
                ssl: Some(row.get::<_, i32>(6)? != 0),
            })
        }).map_err(|e| format!("Datasource '{}' not found: {}", id, e))?;

        Ok(result)
    }

    /// Get a datasource record by ID (without password, for API response).
    pub fn get_record(&self, id: &str) -> Result<DatasourceRecord, String> {
        let conn = self.conn.lock().map_err(|e| format!("Lock failed: {}", e))?;
        let mut stmt = conn
            .prepare("SELECT id, name, db_type, host, port, database_name, username, ssl, created_at, updated_at FROM datasources WHERE id = ?1")
            .map_err(|e| format!("Prepare failed: {}", e))?;

        let result = stmt.query_row(params![id], |row| {
            Ok(DatasourceRecord {
                id: row.get(0)?,
                name: row.get(1)?,
                db_type: row.get(2)?,
                host: row.get(3)?,
                port: row.get(4)?,
                database: row.get(5)?,
                username: row.get(6)?,
                ssl: row.get::<_, i32>(7)? != 0,
                created_at: row.get(8)?,
                updated_at: row.get(9)?,
            })
        }).map_err(|e| format!("Datasource '{}' not found: {}", id, e))?;

        Ok(result)
    }

    /// Update a datasource.
    pub fn update(&self, id: &str, ds: &CreateDatasource) -> Result<(), String> {
        let now = Utc::now().to_rfc3339();
        let db_type = ds.db_type.as_deref().unwrap_or("mysql");
        let port = ds.port.unwrap_or(3306);
        let ssl = if ds.ssl.unwrap_or(false) { 1 } else { 0 };
        let password_enc = base64_encode(&ds.password);

        let conn = self.conn.lock().map_err(|e| format!("Lock failed: {}", e))?;
        let affected = conn.execute(
            "UPDATE datasources SET name=?1, db_type=?2, host=?3, port=?4, database_name=?5, username=?6, password_encrypted=?7, ssl=?8, updated_at=?9 WHERE id=?10",
            params![ds.name, db_type, ds.host, port, ds.database, ds.username, password_enc, ssl, now, id],
        ).map_err(|e| format!("Update failed: {}", e))?;

        if affected == 0 {
            return Err(format!("Datasource '{}' not found", id));
        }
        Ok(())
    }

    /// Delete a datasource.
    pub fn delete(&self, id: &str) -> Result<(), String> {
        let conn = self.conn.lock().map_err(|e| format!("Lock failed: {}", e))?;
        let affected = conn
            .execute("DELETE FROM datasources WHERE id = ?1", params![id])
            .map_err(|e| format!("Delete failed: {}", e))?;

        if affected == 0 {
            return Err(format!("Datasource '{}' not found", id));
        }
        Ok(())
    }
}

// ── Simple Base64 obfuscation (not cryptographic security, just avoids plaintext) ──

fn base64_encode(s: &str) -> String {
    base64_write(s.as_bytes())
}

fn base64_decode(s: &str) -> String {
    base64_read(s).unwrap_or_else(|_| s.to_string())
}

/// Minimal base64 encode (no external crate needed)
fn base64_write(input: &[u8]) -> String {
    const CHARS: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::new();
    for chunk in input.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = if chunk.len() > 1 { chunk[1] as u32 } else { 0 };
        let b2 = if chunk.len() > 2 { chunk[2] as u32 } else { 0 };
        let triple = (b0 << 16) | (b1 << 8) | b2;
        output.push(CHARS[((triple >> 18) & 0x3F) as usize] as char);
        output.push(CHARS[((triple >> 12) & 0x3F) as usize] as char);
        if chunk.len() > 1 { output.push(CHARS[((triple >> 6) & 0x3F) as usize] as char); } else { output.push('='); }
        if chunk.len() > 2 { output.push(CHARS[(triple & 0x3F) as usize] as char); } else { output.push('='); }
    }
    output
}

/// Minimal base64 decode
fn base64_read(input: &str) -> Result<String, String> {
    let input = input.trim_end_matches('=');
    let mut output = Vec::new();
    let chars: Vec<u8> = input.bytes().map(|b| {
        match b {
            b'A'..=b'Z' => b - b'A',
            b'a'..=b'z' => b - b'a' + 26,
            b'0'..=b'9' => b - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => 0,
        }
    }).collect();

    for chunk in chars.chunks(4) {
        let b0 = chunk[0] as u32;
        let b1 = chunk[1] as u32;
        let b2 = if chunk.len() > 2 { chunk[2] as u32 } else { 0 };
        let b3 = if chunk.len() > 3 { chunk[3] as u32 } else { 0 };
        let triple = (b0 << 18) | (b1 << 12) | (b2 << 6) | b3;
        output.push(((triple >> 16) & 0xFF) as u8);
        if chunk.len() > 2 { output.push(((triple >> 8) & 0xFF) as u8); }
        if chunk.len() > 3 { output.push((triple & 0xFF) as u8); }
    }

    String::from_utf8(output).map_err(|e| format!("Invalid UTF-8: {}", e))
}
