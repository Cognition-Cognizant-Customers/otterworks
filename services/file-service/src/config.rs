use std::env;

#[derive(Clone, Debug)]
pub struct AppConfig {
    pub server: ServerConfig,
    pub aws: AwsConfig,
    pub sns: SnsConfig,
}

#[derive(Clone, Debug)]
pub struct ServerConfig {
    pub port: u16,
    pub max_upload_bytes: u64,
}

#[derive(Clone, Debug)]
pub struct AwsConfig {
    pub region: String,
    pub endpoint_url: Option<String>,
    pub s3_bucket: String,
    pub dynamodb_table: String,
    pub dynamodb_folders_table: String,
    pub dynamodb_versions_table: String,
    pub dynamodb_shares_table: String,
}

#[derive(Clone, Debug)]
pub struct SnsConfig {
    pub topic_arn: Option<String>,
}

impl AppConfig {
    pub fn from_env() -> Self {
        Self {
            server: ServerConfig::from_env(),
            aws: AwsConfig::from_env(),
            sns: SnsConfig::from_env(),
        }
    }
}

impl ServerConfig {
    pub fn from_env() -> Self {
        Self {
            port: env::var("PORT")
                .unwrap_or_else(|_| "8082".into())
                .parse()
                .unwrap_or(8082),
            max_upload_bytes: env::var("MAX_UPLOAD_BYTES")
                .unwrap_or_else(|_| "104857600".into()) // 100 MB
                .parse()
                .unwrap_or(104_857_600),
        }
    }
}

impl AwsConfig {
    pub fn from_env() -> Self {
        Self {
            region: env::var("AWS_REGION").unwrap_or_else(|_| "us-east-1".into()),
            endpoint_url: env::var("AWS_ENDPOINT_URL").ok(),
            s3_bucket: env::var("S3_BUCKET").unwrap_or_else(|_| "otterworks-files".into()),
            dynamodb_table: env::var("DYNAMODB_TABLE")
                .unwrap_or_else(|_| "otterworks-file-metadata".into()),
            dynamodb_folders_table: env::var("DYNAMODB_FOLDERS_TABLE")
                .unwrap_or_else(|_| "otterworks-folders".into()),
            dynamodb_versions_table: env::var("DYNAMODB_VERSIONS_TABLE")
                .unwrap_or_else(|_| "otterworks-file-versions".into()),
            dynamodb_shares_table: env::var("DYNAMODB_SHARES_TABLE")
                .unwrap_or_else(|_| "otterworks-file-shares".into()),
        }
    }
}

impl SnsConfig {
    pub fn from_env() -> Self {
        Self {
            topic_arn: env::var("SNS_TOPIC_ARN").ok(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Mutex, MutexGuard, OnceLock};

    const DEFAULT_MAX_UPLOAD_BYTES: u64 = 104_857_600;
    const DEFAULT_PORT: u16 = 8082;

    /// Serialises every test that touches the process environment, so the
    /// suite behaves identically under `--test-threads=1` and in parallel.
    fn env_guard() -> MutexGuard<'static, ()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// Applies `vars` (`None` means "unset"), reads the config, then restores
    /// whatever the environment held before. Nothing is asserted while the
    /// environment is dirty, so a failing assertion cannot leak into siblings.
    fn with_env<T>(vars: &[(&str, Option<&str>)], read: impl FnOnce() -> T) -> T {
        let _guard = env_guard();
        let restore: Vec<(String, Option<String>)> = vars
            .iter()
            .map(|(key, _)| ((*key).to_string(), env::var(key).ok()))
            .collect();
        apply(vars.iter().map(|(k, v)| (*k, *v)));
        let value = read();
        apply(restore.iter().map(|(k, v)| (k.as_str(), v.as_deref())));
        value
    }

    fn apply<'a>(vars: impl Iterator<Item = (&'a str, Option<&'a str>)>) {
        for (key, value) in vars {
            match value {
                Some(value) => env::set_var(key, value),
                None => env::remove_var(key),
            }
        }
    }

    fn server_config(max_upload_bytes: Option<&str>, port: Option<&str>) -> ServerConfig {
        with_env(
            &[("MAX_UPLOAD_BYTES", max_upload_bytes), ("PORT", port)],
            ServerConfig::from_env,
        )
    }

    const AWS_VARS: [&str; 7] = [
        "AWS_REGION",
        "AWS_ENDPOINT_URL",
        "S3_BUCKET",
        "DYNAMODB_TABLE",
        "DYNAMODB_FOLDERS_TABLE",
        "DYNAMODB_VERSIONS_TABLE",
        "DYNAMODB_SHARES_TABLE",
    ];

    fn aws_config(overrides: &[(&str, &str)]) -> AwsConfig {
        let mut vars: Vec<(&str, Option<&str>)> = AWS_VARS.iter().map(|key| (*key, None)).collect();
        for (key, value) in overrides {
            for var in vars.iter_mut() {
                if var.0 == *key {
                    var.1 = Some(value);
                }
            }
        }
        with_env(&vars, AwsConfig::from_env)
    }

    #[test]
    fn test_fileservice_max_upload_bytes_unset_defaults_to_100mb() {
        let config = server_config(None, None);
        assert_eq!(config.max_upload_bytes, DEFAULT_MAX_UPLOAD_BYTES);
    }

    #[test]
    fn test_fileservice_max_upload_bytes_numeric_is_honoured() {
        let config = server_config(Some("2048"), None);
        assert_eq!(config.max_upload_bytes, 2048);
    }

    #[test]
    fn test_fileservice_max_upload_bytes_non_numeric_falls_back_to_default() {
        let config = server_config(Some("one hundred megabytes"), None);
        assert_eq!(config.max_upload_bytes, DEFAULT_MAX_UPLOAD_BYTES);
    }

    #[test]
    fn test_fileservice_max_upload_bytes_negative_falls_back_to_default() {
        let config = server_config(Some("-1"), None);
        assert_eq!(config.max_upload_bytes, DEFAULT_MAX_UPLOAD_BYTES);
    }

    #[test]
    fn test_fileservice_max_upload_bytes_empty_falls_back_to_default() {
        let config = server_config(Some(""), None);
        assert_eq!(config.max_upload_bytes, DEFAULT_MAX_UPLOAD_BYTES);
    }

    #[test]
    fn test_fileservice_port_unset_defaults_to_8082() {
        let config = server_config(None, None);
        assert_eq!(config.port, DEFAULT_PORT);
    }

    #[test]
    fn test_fileservice_port_numeric_is_honoured() {
        let config = server_config(None, Some("9090"));
        assert_eq!(config.port, 9090);
    }

    #[test]
    fn test_fileservice_port_non_numeric_falls_back_to_8082() {
        let config = server_config(None, Some("http"));
        assert_eq!(config.port, DEFAULT_PORT);
    }

    #[test]
    fn test_fileservice_port_above_u16_range_falls_back_to_8082() {
        let config = server_config(None, Some("70000"));
        assert_eq!(config.port, DEFAULT_PORT);
    }

    #[test]
    fn test_fileservice_aws_env_unset_uses_documented_defaults() {
        let config = aws_config(&[]);
        assert_eq!(config.region, "us-east-1");
        assert_eq!(config.endpoint_url, None);
        assert_eq!(config.s3_bucket, "otterworks-files");
        assert_eq!(config.dynamodb_table, "otterworks-file-metadata");
        assert_eq!(config.dynamodb_folders_table, "otterworks-folders");
        assert_eq!(config.dynamodb_versions_table, "otterworks-file-versions");
        assert_eq!(config.dynamodb_shares_table, "otterworks-file-shares");
    }

    #[test]
    fn test_fileservice_aws_env_set_overrides_defaults() {
        let config = aws_config(&[
            ("AWS_REGION", "eu-west-2"),
            ("AWS_ENDPOINT_URL", "http://localstack:4566"),
            ("S3_BUCKET", "tenant-files"),
            ("DYNAMODB_TABLE", "tenant-file-metadata"),
        ]);
        assert_eq!(config.region, "eu-west-2");
        assert_eq!(
            config.endpoint_url.as_deref(),
            Some("http://localstack:4566")
        );
        assert_eq!(config.s3_bucket, "tenant-files");
        assert_eq!(config.dynamodb_table, "tenant-file-metadata");
        assert_eq!(config.dynamodb_folders_table, "otterworks-folders");
    }
}
