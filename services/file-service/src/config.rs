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

/// WP-02 — environment parsing: defaults, overrides, invalid values and the
/// `MAX_UPLOAD_BYTES` boundary trio.
#[cfg(test)]
mod env_parsing_tests {
    use super::*;
    use std::panic::{catch_unwind, resume_unwind, AssertUnwindSafe};
    use std::sync::Mutex;

    /// Every variable the config layer reads.
    const MANAGED_VARS: [&str; 10] = [
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

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    /// Runs `body` with exactly `vars` present and every other managed
    /// variable cleared, then restores the process environment.
    ///
    /// The environment is process-global, so these cases are serialised and
    /// each one states the *complete* environment it wants. That makes every
    /// case independent of the order the suite runs in and of whatever the
    /// developer happens to have exported. The lock is deliberately not
    /// poisoned by a failing assertion, so one failure cannot cascade.
    fn with_env<T>(vars: &[(&str, &str)], body: impl FnOnce() -> T) -> T {
        let guard = ENV_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());

        let saved: Vec<(&str, Option<String>)> = MANAGED_VARS
            .iter()
            .map(|key| (*key, env::var(key).ok()))
            .collect();
        for key in MANAGED_VARS {
            env::remove_var(key);
        }
        for (key, value) in vars {
            env::set_var(key, value);
        }

        let outcome = catch_unwind(AssertUnwindSafe(body));

        for (key, value) in saved {
            match value {
                Some(value) => env::set_var(key, value),
                None => env::remove_var(key),
            }
        }
        drop(guard);

        match outcome {
            Ok(value) => value,
            Err(panic) => resume_unwind(panic),
        }
    }

    const DEFAULT_PORT: u16 = 8082;
    const DEFAULT_MAX_UPLOAD: u64 = 104_857_600;

    // -- Defaults --

    #[test]
    fn server_defaults_apply_when_nothing_is_set() {
        let config = with_env(&[], ServerConfig::from_env);

        assert_eq!(config.port, DEFAULT_PORT);
        assert_eq!(config.max_upload_bytes, DEFAULT_MAX_UPLOAD);
    }

    #[test]
    fn aws_defaults_apply_when_nothing_is_set() {
        let config = with_env(&[], AwsConfig::from_env);

        assert_eq!(config.region, "us-east-1");
        assert_eq!(config.endpoint_url, None);
        assert_eq!(config.s3_bucket, "otterworks-files");
        assert_eq!(config.dynamodb_table, "otterworks-file-metadata");
        assert_eq!(config.dynamodb_folders_table, "otterworks-folders");
        assert_eq!(config.dynamodb_versions_table, "otterworks-file-versions");
        assert_eq!(config.dynamodb_shares_table, "otterworks-file-shares");
    }

    #[test]
    fn the_four_dynamodb_tables_default_to_distinct_names() {
        let config = with_env(&[], AwsConfig::from_env);

        let mut names = vec![
            config.dynamodb_table,
            config.dynamodb_folders_table,
            config.dynamodb_versions_table,
            config.dynamodb_shares_table,
        ];
        names.sort();
        let distinct = {
            let mut deduped = names.clone();
            deduped.dedup();
            deduped.len()
        };
        assert_eq!(distinct, 4, "tables must not collide: {names:?}");
    }

    #[test]
    fn sns_is_disabled_by_default() {
        assert_eq!(with_env(&[], SnsConfig::from_env).topic_arn, None);
    }

    #[test]
    fn app_config_composes_all_three_sections() {
        let config = with_env(
            &[
                ("PORT", "9001"),
                ("AWS_REGION", "eu-west-2"),
                ("SNS_TOPIC_ARN", "arn:aws:sns:eu-west-2:1:files"),
            ],
            AppConfig::from_env,
        );

        assert_eq!(config.server.port, 9001);
        assert_eq!(config.aws.region, "eu-west-2");
        assert_eq!(
            config.sns.topic_arn.as_deref(),
            Some("arn:aws:sns:eu-west-2:1:files")
        );
        assert_eq!(
            config.server.max_upload_bytes, DEFAULT_MAX_UPLOAD,
            "unset values keep their defaults when siblings are overridden"
        );
    }

    // -- Overrides --

    #[test]
    fn every_aws_setting_can_be_overridden() {
        let config = with_env(
            &[
                ("AWS_REGION", "ap-south-1"),
                ("AWS_ENDPOINT_URL", "http://localstack:4566"),
                ("S3_BUCKET", "tenant-files"),
                ("DYNAMODB_TABLE", "tenant-metadata"),
                ("DYNAMODB_FOLDERS_TABLE", "tenant-folders"),
                ("DYNAMODB_VERSIONS_TABLE", "tenant-versions"),
                ("DYNAMODB_SHARES_TABLE", "tenant-shares"),
            ],
            AwsConfig::from_env,
        );

        assert_eq!(config.region, "ap-south-1");
        assert_eq!(
            config.endpoint_url.as_deref(),
            Some("http://localstack:4566")
        );
        assert_eq!(config.s3_bucket, "tenant-files");
        assert_eq!(config.dynamodb_table, "tenant-metadata");
        assert_eq!(config.dynamodb_folders_table, "tenant-folders");
        assert_eq!(config.dynamodb_versions_table, "tenant-versions");
        assert_eq!(config.dynamodb_shares_table, "tenant-shares");
    }

    /// An exported-but-empty variable is a common deployment slip. String
    /// settings take it literally, which is worth knowing: an empty bucket
    /// name reaches the S3 client rather than falling back to the default.
    #[test]
    fn an_empty_string_setting_is_taken_literally() {
        let config = with_env(
            &[
                ("S3_BUCKET", ""),
                ("AWS_REGION", ""),
                ("AWS_ENDPOINT_URL", ""),
            ],
            AwsConfig::from_env,
        );

        assert_eq!(config.s3_bucket, "");
        assert_eq!(config.region, "");
        assert_eq!(
            config.endpoint_url.as_deref(),
            Some(""),
            "an empty endpoint override is not the same as no override"
        );
    }

    #[test]
    fn an_empty_sns_topic_is_still_treated_as_configured() {
        assert_eq!(
            with_env(&[("SNS_TOPIC_ARN", "")], SnsConfig::from_env).topic_arn,
            Some(String::new())
        );
    }

    // -- PORT --

    #[test]
    fn a_valid_port_is_honoured() {
        assert_eq!(
            with_env(&[("PORT", "9090")], ServerConfig::from_env).port,
            9090
        );
    }

    /// Boundary trio for the u16 port space: max-1 and max parse, max+1 does
    /// not and silently falls back to the default.
    #[test]
    fn port_boundary_trio() {
        for (raw, expected) in [
            ("65534", 65534u16),
            ("65535", 65535),
            ("65536", DEFAULT_PORT),
        ] {
            assert_eq!(
                with_env(&[("PORT", raw)], ServerConfig::from_env).port,
                expected,
                "PORT={raw}"
            );
        }
    }

    #[test]
    fn port_zero_is_accepted_verbatim() {
        // Port 0 asks the OS for an ephemeral port: not a parse failure, so
        // no default is substituted.
        assert_eq!(with_env(&[("PORT", "0")], ServerConfig::from_env).port, 0);
    }

    #[test]
    fn invalid_ports_fall_back_to_the_default() {
        for raw in ["", " ", "http", "-1", "8082.0", " 8082", "8082 ", "+8082x"] {
            assert_eq!(
                with_env(&[("PORT", raw)], ServerConfig::from_env).port,
                DEFAULT_PORT,
                "PORT={raw:?}"
            );
        }
    }

    // -- MAX_UPLOAD_BYTES --

    #[test]
    fn a_valid_upload_limit_is_honoured() {
        assert_eq!(
            with_env(&[("MAX_UPLOAD_BYTES", "5242880")], ServerConfig::from_env).max_upload_bytes,
            5_242_880
        );
    }

    /// Boundary trio around the configured limit itself: one byte under, the
    /// limit, and one byte over are all valid u64 values and must be taken
    /// exactly as given - no rounding, no clamping to the 100 MB default.
    #[test]
    fn max_upload_bytes_boundary_trio_around_the_default_limit() {
        for delta in [-1i64, 0, 1] {
            let value = (DEFAULT_MAX_UPLOAD as i64 + delta) as u64;
            assert_eq!(
                with_env(
                    &[("MAX_UPLOAD_BYTES", &value.to_string())],
                    ServerConfig::from_env
                )
                .max_upload_bytes,
                value,
                "MAX_UPLOAD_BYTES={value}"
            );
        }
    }

    /// Boundary trio at the top of the u64 range: max-1 and max parse, max+1
    /// overflows and is silently replaced by the default.
    #[test]
    fn max_upload_bytes_boundary_trio_at_the_top_of_the_range() {
        for (raw, expected) in [
            ("18446744073709551614", u64::MAX - 1),
            ("18446744073709551615", u64::MAX),
            ("18446744073709551616", DEFAULT_MAX_UPLOAD),
        ] {
            assert_eq!(
                with_env(&[("MAX_UPLOAD_BYTES", raw)], ServerConfig::from_env).max_upload_bytes,
                expected,
                "MAX_UPLOAD_BYTES={raw}"
            );
        }
    }

    /// Zero parses, so the service starts with every upload rejected. Worth
    /// pinning: it is the one "valid" value that disables the feature.
    #[test]
    fn a_zero_upload_limit_is_accepted() {
        assert_eq!(
            with_env(&[("MAX_UPLOAD_BYTES", "0")], ServerConfig::from_env).max_upload_bytes,
            0
        );
    }

    /// WP-02 finding F8 (design gap, pinned not fixed): a malformed limit is
    /// swallowed and replaced by the 100 MB default. A deployment that meant
    /// to set a 10 MB cap and wrote `10MB` silently runs a 100 MB cap instead
    /// of failing to start.
    #[test]
    fn malformed_upload_limits_silently_fall_back_to_the_default() {
        for raw in [
            "",
            " ",
            "10MB",
            "100 MB",
            "-1",
            "1e6",
            "104857600.0",
            " 104857600",
            "104_857_600",
            "0x1000",
        ] {
            assert_eq!(
                with_env(&[("MAX_UPLOAD_BYTES", raw)], ServerConfig::from_env).max_upload_bytes,
                DEFAULT_MAX_UPLOAD,
                "MAX_UPLOAD_BYTES={raw:?} should not have parsed"
            );
        }
    }

    #[test]
    fn a_plus_prefixed_limit_is_accepted_by_the_integer_parser() {
        // `u64::from_str` allows a leading `+`; documented so the tolerated
        // and rejected spellings above are unambiguous.
        assert_eq!(
            with_env(&[("MAX_UPLOAD_BYTES", "+2048")], ServerConfig::from_env).max_upload_bytes,
            2048
        );
    }

    #[test]
    fn server_config_is_independent_of_the_aws_variables() {
        let config = with_env(
            &[("S3_BUCKET", "irrelevant"), ("AWS_REGION", "eu-central-1")],
            ServerConfig::from_env,
        );

        assert_eq!(config.port, DEFAULT_PORT);
        assert_eq!(config.max_upload_bytes, DEFAULT_MAX_UPLOAD);
    }
}
