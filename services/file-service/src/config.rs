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
    use std::sync::Mutex;

    /// The process environment is global, so env-driven tests are serialised.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    const VARS: &[&str] = &[
        "PORT",
        "MAX_UPLOAD_BYTES",
        "AWS_REGION",
        "AWS_ENDPOINT_URL",
        "S3_BUCKET",
        "DYNAMODB_TABLE",
        "DYNAMODB_FOLDERS_TABLE",
        "DYNAMODB_VERSIONS_TABLE",
        "DYNAMODB_SHARES_TABLE",
        "SNS_TOPIC_ARN",
    ];

    /// Run `f` with only `vars` set, restoring the previous environment after.
    fn with_env<T>(vars: &[(&str, &str)], f: impl FnOnce() -> T) -> T {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());

        let saved: Vec<(&str, Option<String>)> =
            VARS.iter().map(|k| (*k, env::var(k).ok())).collect();
        for key in VARS {
            env::remove_var(key);
        }
        for (key, value) in vars {
            env::set_var(key, value);
        }

        let result = f();

        for (key, value) in saved {
            match value {
                Some(value) => env::set_var(key, value),
                None => env::remove_var(key),
            }
        }
        result
    }

    #[test]
    fn missing_environment_falls_back_to_defaults() {
        let config = with_env(&[], AppConfig::from_env);

        assert_eq!(config.server.port, 8082);
        assert_eq!(config.server.max_upload_bytes, 104_857_600);
        assert_eq!(config.aws.region, "us-east-1");
        assert_eq!(config.aws.endpoint_url, None);
        assert_eq!(config.aws.s3_bucket, "otterworks-files");
        assert_eq!(config.aws.dynamodb_table, "otterworks-file-metadata");
        assert_eq!(config.aws.dynamodb_folders_table, "otterworks-folders");
        assert_eq!(
            config.aws.dynamodb_versions_table,
            "otterworks-file-versions"
        );
        assert_eq!(config.aws.dynamodb_shares_table, "otterworks-file-shares");
        assert_eq!(config.sns.topic_arn, None);
    }

    #[test]
    fn environment_overrides_every_setting() {
        let config = with_env(
            &[
                ("PORT", "9090"),
                ("MAX_UPLOAD_BYTES", "2048"),
                ("AWS_REGION", "eu-west-1"),
                ("AWS_ENDPOINT_URL", "http://localstack:4566"),
                ("S3_BUCKET", "tenant-files"),
                ("DYNAMODB_TABLE", "tenant-files-meta"),
                ("DYNAMODB_FOLDERS_TABLE", "tenant-folders"),
                ("DYNAMODB_VERSIONS_TABLE", "tenant-versions"),
                ("DYNAMODB_SHARES_TABLE", "tenant-shares"),
                ("SNS_TOPIC_ARN", "arn:aws:sns:eu-west-1:1:file-events"),
            ],
            AppConfig::from_env,
        );

        assert_eq!(config.server.port, 9090);
        assert_eq!(config.server.max_upload_bytes, 2048);
        assert_eq!(config.aws.region, "eu-west-1");
        assert_eq!(
            config.aws.endpoint_url.as_deref(),
            Some("http://localstack:4566")
        );
        assert_eq!(config.aws.s3_bucket, "tenant-files");
        assert_eq!(config.aws.dynamodb_table, "tenant-files-meta");
        assert_eq!(config.aws.dynamodb_folders_table, "tenant-folders");
        assert_eq!(config.aws.dynamodb_versions_table, "tenant-versions");
        assert_eq!(config.aws.dynamodb_shares_table, "tenant-shares");
        assert_eq!(
            config.sns.topic_arn.as_deref(),
            Some("arn:aws:sns:eu-west-1:1:file-events")
        );
    }

    #[test]
    fn unparseable_numeric_settings_fall_back_to_defaults() {
        let config = with_env(
            &[("PORT", "not-a-port"), ("MAX_UPLOAD_BYTES", "-1")],
            ServerConfig::from_env,
        );

        assert_eq!(config.port, 8082);
        assert_eq!(config.max_upload_bytes, 104_857_600);
    }

    #[test]
    fn an_empty_endpoint_url_is_still_honoured_as_set() {
        // `AWS_ENDPOINT_URL=""` is distinct from an unset variable: the SDK is
        // handed an empty override rather than the default endpoint resolver.
        let config = with_env(&[("AWS_ENDPOINT_URL", "")], AwsConfig::from_env);
        assert_eq!(config.endpoint_url.as_deref(), Some(""));
    }
}
