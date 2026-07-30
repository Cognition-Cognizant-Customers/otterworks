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
    use super::{AwsConfig, ServerConfig, SnsConfig};
    use std::env;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    fn with_env<F>(values: &[(&str, Option<&str>)], test: F)
    where
        F: FnOnce(),
    {
        let _lock = ENV_LOCK.lock().expect("environment lock");
        let previous: Vec<_> = values
            .iter()
            .map(|(name, _)| (*name, env::var(name).ok()))
            .collect();

        for (name, value) in values {
            match value {
                Some(value) => env::set_var(name, value),
                None => env::remove_var(name),
            }
        }

        test();

        for (name, value) in previous {
            match value {
                Some(value) => env::set_var(name, value),
                None => env::remove_var(name),
            }
        }
    }

    #[test]
    fn server_config_uses_defaults() {
        with_env(&[("PORT", None), ("MAX_UPLOAD_BYTES", None)], || {
            let config = ServerConfig::from_env();

            assert_eq!(config.port, 8082);
            assert_eq!(config.max_upload_bytes, 104_857_600);
        });
    }

    #[test]
    fn server_config_reads_environment_overrides() {
        with_env(
            &[("PORT", Some("9000")), ("MAX_UPLOAD_BYTES", Some("12345"))],
            || {
                let config = ServerConfig::from_env();

                assert_eq!(config.port, 9000);
                assert_eq!(config.max_upload_bytes, 12345);
            },
        );
    }

    #[test]
    fn aws_config_uses_defaults() {
        with_env(
            &[
                ("AWS_REGION", None),
                ("AWS_ENDPOINT_URL", None),
                ("S3_BUCKET", None),
                ("DYNAMODB_TABLE", None),
                ("DYNAMODB_FOLDERS_TABLE", None),
                ("DYNAMODB_VERSIONS_TABLE", None),
                ("DYNAMODB_SHARES_TABLE", None),
            ],
            || {
                let config = AwsConfig::from_env();

                assert_eq!(config.region, "us-east-1");
                assert_eq!(config.endpoint_url, None);
                assert_eq!(config.s3_bucket, "otterworks-files");
                assert_eq!(config.dynamodb_table, "otterworks-file-metadata");
                assert_eq!(config.dynamodb_folders_table, "otterworks-folders");
                assert_eq!(config.dynamodb_versions_table, "otterworks-file-versions");
                assert_eq!(config.dynamodb_shares_table, "otterworks-file-shares");
            },
        );
    }

    #[test]
    fn aws_config_reads_environment_overrides() {
        with_env(
            &[
                ("AWS_REGION", Some("eu-west-1")),
                ("AWS_ENDPOINT_URL", Some("http://localhost:4566")),
                ("S3_BUCKET", Some("test-files")),
                ("DYNAMODB_TABLE", Some("test-metadata")),
                ("DYNAMODB_FOLDERS_TABLE", Some("test-folders")),
                ("DYNAMODB_VERSIONS_TABLE", Some("test-versions")),
                ("DYNAMODB_SHARES_TABLE", Some("test-shares")),
            ],
            || {
                let config = AwsConfig::from_env();

                assert_eq!(config.region, "eu-west-1");
                assert_eq!(
                    config.endpoint_url.as_deref(),
                    Some("http://localhost:4566")
                );
                assert_eq!(config.s3_bucket, "test-files");
                assert_eq!(config.dynamodb_table, "test-metadata");
                assert_eq!(config.dynamodb_folders_table, "test-folders");
                assert_eq!(config.dynamodb_versions_table, "test-versions");
                assert_eq!(config.dynamodb_shares_table, "test-shares");
            },
        );
    }

    #[test]
    fn sns_config_uses_default_none() {
        with_env(&[("SNS_TOPIC_ARN", None)], || {
            assert_eq!(SnsConfig::from_env().topic_arn, None);
        });
    }

    #[test]
    fn sns_config_reads_environment_override() {
        with_env(
            &[("SNS_TOPIC_ARN", Some("arn:aws:sns:us-east-1:123:events"))],
            || {
                assert_eq!(
                    SnsConfig::from_env().topic_arn.as_deref(),
                    Some("arn:aws:sns:us-east-1:123:events")
                );
            },
        );
    }
}
