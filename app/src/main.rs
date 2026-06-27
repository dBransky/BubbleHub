use tauri::{WebviewUrl, WebviewWindowBuilder};

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let url = app_url()?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("AgeOS Control Center")
                .inner_size(1280.0, 860.0)
                .min_inner_size(960.0, 640.0)
                .resizable(true)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("failed to run AgeOS Control Center");
}

fn app_url() -> Result<url::Url, Box<dyn std::error::Error>> {
    app_url_from(std::env::args().nth(1), std::env::var("AGEOS_APP_URL").ok())
}

fn app_url_from(
    arg_url: Option<String>,
    env_url: Option<String>,
) -> Result<url::Url, Box<dyn std::error::Error>> {
    let raw = arg_url
        .or(env_url)
        .unwrap_or_else(|| "http://127.0.0.1:8010/".to_string());
    Ok(raw.parse()?)
}

#[cfg(test)]
mod tests {
    use super::app_url_from;

    #[test]
    fn app_url_prefers_cli_arg() {
        let url = app_url_from(
            Some("http://127.0.0.1:9000/".to_string()),
            Some("http://127.0.0.1:8010/".to_string()),
        )
        .expect("url should parse");

        assert_eq!(url.as_str(), "http://127.0.0.1:9000/");
    }

    #[test]
    fn app_url_uses_env_when_arg_missing() {
        let url = app_url_from(None, Some("http://127.0.0.1:7777/".to_string())).expect("url should parse");

        assert_eq!(url.as_str(), "http://127.0.0.1:7777/");
    }

    #[test]
    fn app_url_defaults_to_local_api() {
        let url = app_url_from(None, None).expect("url should parse");

        assert_eq!(url.as_str(), "http://127.0.0.1:8010/");
    }

    #[test]
    fn app_url_rejects_invalid_urls() {
        assert!(app_url_from(Some("not a url".to_string()), None).is_err());
    }
}
