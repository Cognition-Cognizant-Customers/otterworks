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
    use std::sync::{Mutex, MutexGuard};

    /// Every environment variable this module reads. `EnvScope` clears and
    /// restores exactly these, so a test never depends on the ambient
    /// environment of the machine running it.
    const MANAGED_VARS: [&str; 9] = [
        "PORT",
        "MAX_UPLOAD_BYTES",
        "AWS_REGION",
        "AWS_ENDPOINT_URL",
        "S3_BUCKET",
        "DYNAMODB_TABLE",
        "DYNAMODB_FOLDERS_TABLE",
        "DYNAMODB_VERSIONS_TABLE",
        "DYNAMODB_SHARES_TABLE",
    ];

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    /// Serializes the process environment across config tests and restores the
    /// previous values on drop, so these tests are order-independent and safe
    /// to run alongside the rest of the suite.
    struct EnvScope {
        _guard: MutexGuard<'static, ()>,
        saved: Vec<(&'static str, Option<String>)>,
    }

    impl EnvScope {
        fn new() -> Self {
            let guard = ENV_LOCK
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            let saved = MANAGED_VARS
                .iter()
                .map(|key| (*key, env::var(key).ok()))
                .collect();
            for key in MANAGED_VARS {
                env::remove_var(key);
            }
            Self {
                _guard: guard,
                saved,
            }
        }

        fn set(&self, key: &str, value: &str) {
            assert!(
                MANAGED_VARS.contains(&key),
                "{key} must be listed in MANAGED_VARS so it is restored"
            );
            env::set_var(key, value);
        }
    }

    impl Drop for EnvScope {
        fn drop(&mut self) {
            for (key, value) in &self.saved {
                match value {
                    Some(v) => env::set_var(key, v),
                    None => env::remove_var(key),
                }
            }
        }
    }

    const DEFAULT_MAX_UPLOAD: u64 = 104_857_600;

    fn max_upload_for(value: &str) -> u64 {
        let env = EnvScope::new();
        env.set("MAX_UPLOAD_BYTES", value);
        ServerConfig::from_env().max_upload_bytes
    }

    fn port_for(value: &str) -> u16 {
        let env = EnvScope::new();
        env.set("PORT", value);
        ServerConfig::from_env().port
    }

    // -- ServerConfig: defaults --

    #[test]
    fn server_config_defaults_when_unset() {
        let _env = EnvScope::new();
        let cfg = ServerConfig::from_env();
        assert_eq!(cfg.port, 8082);
        assert_eq!(cfg.max_upload_bytes, DEFAULT_MAX_UPLOAD);
    }

    // -- ServerConfig: PORT --

    #[test]
    fn port_valid_override() {
        assert_eq!(port_for("9090"), 9090);
    }

    #[test]
    fn port_boundaries() {
        // u16 range: 0 .. 65535. 65536 is one past the top of the type.
        assert_eq!(port_for("0"), 0, "port 0 is accepted verbatim");
        assert_eq!(port_for("1"), 1);
        assert_eq!(port_for("65534"), 65534);
        assert_eq!(port_for("65535"), 65535);
        assert_eq!(
            port_for("65536"),
            8082,
            "out-of-range port silently falls back to the default"
        );
    }

    #[test]
    fn port_invalid_values_fall_back_to_default() {
        for value in ["", " ", "-1", "80.5", "eighty", "8082 ", "0x1f90"] {
            assert_eq!(
                port_for(value),
                8082,
                "PORT={value:?} should fall back to the default"
            );
        }
    }

    // -- ServerConfig: MAX_UPLOAD_BYTES --

    #[test]
    fn max_upload_bytes_valid_override() {
        assert_eq!(max_upload_for("1048576"), 1_048_576);
    }

    #[test]
    fn max_upload_bytes_boundaries_around_default() {
        assert_eq!(max_upload_for("104857599"), DEFAULT_MAX_UPLOAD - 1);
        assert_eq!(max_upload_for("104857600"), DEFAULT_MAX_UPLOAD);
        assert_eq!(max_upload_for("104857601"), DEFAULT_MAX_UPLOAD + 1);
    }

    #[test]
    fn max_upload_bytes_accepts_u64_max_and_rejects_one_past_it() {
        assert_eq!(max_upload_for(&u64::MAX.to_string()), u64::MAX);
        // u64::MAX + 1 does not parse, so the default is used.
        assert_eq!(max_upload_for("18446744073709551616"), DEFAULT_MAX_UPLOAD);
    }

    #[test]
    fn max_upload_bytes_invalid_values_fall_back_to_default() {
        for value in ["", " ", "-1", "-104857600", "1.5", "100MB", "100_000"] {
            assert_eq!(
                max_upload_for(value),
                DEFAULT_MAX_UPLOAD,
                "MAX_UPLOAD_BYTES={value:?} should fall back to the default"
            );
        }
    }

    #[test]
    fn max_upload_bytes_zero_is_accepted() {
        // Documents current behavior: 0 disables every non-empty upload
        // (handlers reject when len > max). See FINDING in
        // max_upload_bytes_zero_should_be_rejected_as_misconfiguration.
        assert_eq!(max_upload_for("0"), 0);
    }

    #[test]
    #[ignore = "FINDING: MAX_UPLOAD_BYTES=0 is accepted and silently disables all uploads; \
                config has no validation and no startup error for a nonsensical limit"]
    fn max_upload_bytes_zero_should_be_rejected_as_misconfiguration() {
        assert_ne!(max_upload_for("0"), 0);
    }

    #[test]
    #[ignore = "FINDING: unparseable PORT / MAX_UPLOAD_BYTES are swallowed by unwrap_or, so a \
                typo in a deployment manifest boots the service on defaults with no log or error"]
    fn unparseable_numeric_config_should_not_be_silently_ignored() {
        // A misconfigured value should surface, not quietly become the default.
        assert_ne!(port_for("not-a-port"), 8082);
    }

    // -- AwsConfig --

    #[test]
    fn aws_config_defaults_when_unset() {
        let _env = EnvScope::new();
        let cfg = AwsConfig::from_env();
        assert_eq!(cfg.region, "us-east-1");
        assert_eq!(cfg.endpoint_url, None);
        assert_eq!(cfg.s3_bucket, "otterworks-files");
        assert_eq!(cfg.dynamodb_table, "otterworks-file-metadata");
        assert_eq!(cfg.dynamodb_folders_table, "otterworks-folders");
        assert_eq!(cfg.dynamodb_versions_table, "otterworks-file-versions");
        assert_eq!(cfg.dynamodb_shares_table, "otterworks-file-shares");
    }

    #[test]
    fn aws_config_valid_overrides() {
        let env = EnvScope::new();
        env.set("AWS_REGION", "eu-west-2");
        env.set("AWS_ENDPOINT_URL", "http://localstack:4566");
        env.set("S3_BUCKET", "tenant-bucket");
        env.set("DYNAMODB_TABLE", "tenant-files");
        env.set("DYNAMODB_FOLDERS_TABLE", "tenant-folders");
        env.set("DYNAMODB_VERSIONS_TABLE", "tenant-versions");
        env.set("DYNAMODB_SHARES_TABLE", "tenant-shares");

        let cfg = AwsConfig::from_env();
        assert_eq!(cfg.region, "eu-west-2");
        assert_eq!(cfg.endpoint_url, Some("http://localstack:4566".to_string()));
        assert_eq!(cfg.s3_bucket, "tenant-bucket");
        assert_eq!(cfg.dynamodb_table, "tenant-files");
        assert_eq!(cfg.dynamodb_folders_table, "tenant-folders");
        assert_eq!(cfg.dynamodb_versions_table, "tenant-versions");
        assert_eq!(cfg.dynamodb_shares_table, "tenant-shares");
    }

    #[test]
    fn aws_config_empty_strings_are_taken_verbatim() {
        // Empty is not the same as unset: `env::var` returns Ok(""), so the
        // default is skipped and the service starts with an unusable region,
        // bucket and table name.
        let env = EnvScope::new();
        env.set("AWS_REGION", "");
        env.set("S3_BUCKET", "");
        env.set("DYNAMODB_TABLE", "");
        env.set("AWS_ENDPOINT_URL", "");

        let cfg = AwsConfig::from_env();
        assert_eq!(cfg.region, "");
        assert_eq!(cfg.s3_bucket, "");
        assert_eq!(cfg.dynamodb_table, "");
        assert_eq!(cfg.endpoint_url, Some(String::new()));
    }

    #[test]
    #[ignore = "FINDING: an empty AWS_REGION / S3_BUCKET / DYNAMODB_* env var is taken verbatim \
                instead of falling back to the default, so an unset-but-declared Helm value \
                boots a service that fails every AWS call at runtime"]
    fn aws_config_empty_strings_should_fall_back_to_defaults() {
        let env = EnvScope::new();
        env.set("AWS_REGION", "");
        env.set("S3_BUCKET", "");
        let cfg = AwsConfig::from_env();
        assert_eq!(cfg.region, "us-east-1");
        assert_eq!(cfg.s3_bucket, "otterworks-files");
    }

    #[test]
    fn aws_endpoint_url_is_none_only_when_unset() {
        let env = EnvScope::new();
        assert_eq!(AwsConfig::from_env().endpoint_url, None);
        env.set("AWS_ENDPOINT_URL", "http://127.0.0.1:4566");
        assert_eq!(
            AwsConfig::from_env().endpoint_url,
            Some("http://127.0.0.1:4566".to_string())
        );
    }

    // -- SnsConfig --

    #[test]
    fn sns_config_topic_arn_default_and_override() {
        let _env = EnvScope::new();
        // SNS_TOPIC_ARN is intentionally not in MANAGED_VARS' clearing set for
        // other tests; manage it locally so nothing leaks.
        let previous = env::var("SNS_TOPIC_ARN").ok();
        env::remove_var("SNS_TOPIC_ARN");
        assert_eq!(SnsConfig::from_env().topic_arn, None);

        env::set_var("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:000000000000:files");
        assert_eq!(
            SnsConfig::from_env().topic_arn,
            Some("arn:aws:sns:us-east-1:000000000000:files".to_string())
        );

        env::set_var("SNS_TOPIC_ARN", "");
        assert_eq!(
            SnsConfig::from_env().topic_arn,
            Some(String::new()),
            "empty ARN is Some(\"\"), not None — publishing would fail at runtime"
        );

        match previous {
            Some(v) => env::set_var("SNS_TOPIC_ARN", v),
            None => env::remove_var("SNS_TOPIC_ARN"),
        }
    }

    // -- AppConfig --

    #[test]
    fn app_config_composes_all_three_sections() {
        let env = EnvScope::new();
        env.set("PORT", "8099");
        env.set("MAX_UPLOAD_BYTES", "2048");
        env.set("AWS_REGION", "ap-southeast-2");
        env.set("S3_BUCKET", "composed-bucket");

        let cfg = AppConfig::from_env();
        assert_eq!(cfg.server.port, 8099);
        assert_eq!(cfg.server.max_upload_bytes, 2048);
        assert_eq!(cfg.aws.region, "ap-southeast-2");
        assert_eq!(cfg.aws.s3_bucket, "composed-bucket");
    }

    #[test]
    fn app_config_is_cloneable_and_debug_printable() {
        let _env = EnvScope::new();
        let cfg = AppConfig::from_env();
        let clone = cfg.clone();
        assert_eq!(clone.server.port, cfg.server.port);
        assert!(format!("{cfg:?}").contains("AppConfig"));
    }
}
