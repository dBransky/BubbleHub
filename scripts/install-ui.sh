#!/usr/bin/env bash

bubblehub_install_choice_file() {
  echo "${BUBBLEHUB_INSTALL_CHOICE_FILE:-.bubblehub-install-app-choice}"
}

bubblehub_install_normalize_yes_no() {
  case "${1,,}" in
    1|true|yes|y|on)
      echo "1"
      ;;
    0|false|no|n|off)
      echo "0"
      ;;
    *)
      echo ""
      ;;
  esac
}

bubblehub_show_desktop_app_config() {
  cat >/dev/tty <<'EOF'

Desktop app configuration
-------------------------
More install options will appear here soon:
  - install location
  - desktop shortcut preferences
  - GPU/runtime defaults

For now, choose whether to install the Tauri desktop app or keep this
installation CLI-only. After install finishes, BubbleHub will ask you to choose
your default base model unless you skip that step with BUBBLEHUB_SKIP_MODEL_SETUP=1.

EOF
}

bubblehub_render_choice_options() {
  local selected="$1"
  local rewind="${2:-0}"
  local labels=(
    "Install BubbleHub Control Center desktop app"
    "Use built-in CLI commands only"
    "Configure install options"
  )
  if [[ "$rewind" == "1" ]]; then
    printf '\033[%dF' "${#labels[@]}" >/dev/tty
  fi
  for i in "${!labels[@]}"; do
    local marker=" "
    local line
    if [[ "$i" == "$selected" ]]; then
      marker=">"
      line="${marker} ${labels[$i]}"
      printf '\033[2K\r\033[32m%s\033[0m\n' "$line" >/dev/tty
    else
      line="${marker} ${labels[$i]}"
      printf '\033[2K\r%s\n' "$line" >/dev/tty
    fi
  done
}

bubblehub_prompt_desktop_app_install() {
  local selected=0
  local rendered=0
  local key rest
  cat >/dev/tty <<'EOF'

BubbleHub installer
We noticed you are using CLI install.
Do you want to install the desktop app as well?
You can always install it later by running: bubblehub app

Use Up/Down arrows and press Enter.
EOF
  while true; do
    bubblehub_render_choice_options "$selected" "$rendered"
    rendered=1
    IFS= read -rsn1 key </dev/tty || key=""
    case "$key" in
      "")
        case "$selected" in
          0) echo "1"; return ;;
          1) echo "0"; return ;;
          2) bubblehub_show_desktop_app_config; rendered=0 ;;
        esac
        ;;
      $'\033')
        IFS= read -rsn2 -t 0.1 rest </dev/tty || rest=""
        case "$rest" in
          "[A") selected=$(((selected + 2) % 3)) ;;
          "[B") selected=$(((selected + 1) % 3)) ;;
        esac
        ;;
      k)
        selected=$(((selected + 2) % 3))
        ;;
      j)
        selected=$(((selected + 1) % 3))
        ;;
      1|y|Y)
        echo "1"
        return
        ;;
      2|n|N)
        echo "0"
        return
        ;;
      c|C)
        bubblehub_show_desktop_app_config
        rendered=0
        ;;
    esac
  done
}

bubblehub_resolve_desktop_app_choice() {
  local explicit normalized choice_file choice
  explicit="${BUBBLEHUB_INSTALL_APP:-}"
  if [[ -n "$explicit" ]]; then
    normalized="$(bubblehub_install_normalize_yes_no "$explicit")"
    if [[ -n "$normalized" ]]; then
      echo "$normalized"
      return
    fi
    echo "Invalid BUBBLEHUB_INSTALL_APP value: $explicit (expected yes/no)." >&2
    exit 1
  fi

  choice_file="$(bubblehub_install_choice_file)"
  if [[ -f "$choice_file" ]]; then
    normalized="$(bubblehub_install_normalize_yes_no "$(tr -d '[:space:]' < "$choice_file")")"
    if [[ -n "$normalized" ]]; then
      echo "$normalized"
      return
    fi
  fi

  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    echo "0"
    return
  fi

  choice="$(bubblehub_prompt_desktop_app_install)"
  printf '%s\n' "$choice" > "$choice_file"
  echo "$choice"
}

bubblehub_run_base_model_setup() {
  if [[ "${BUBBLEHUB_SKIP_MODEL_SETUP:-0}" == "1" ]]; then
    return 0
  fi
  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    return 0
  fi
  if ! command -v bubblehub >/dev/null 2>&1; then
    return 0
  fi
  echo
  echo "Choose your default base model for BubbleHub."
  bubblehub models setup </dev/tty >/dev/tty 2>&1 || true
}
