#!/usr/bin/env bash

ageos_install_choice_file() {
  echo "${AGEOS_INSTALL_CHOICE_FILE:-.ageos-install-app-choice}"
}

ageos_install_normalize_yes_no() {
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

ageos_show_desktop_app_config() {
  cat >/dev/tty <<'EOF'

Desktop app configuration
-------------------------
More install options will appear here soon:
  - install location
  - desktop shortcut preferences
  - GPU/runtime defaults

For now, choose whether to install the Tauri desktop app or keep this
installation CLI-only.

EOF
}

ageos_render_choice_options() {
  local selected="$1"
  local rewind="${2:-0}"
  local labels=(
    "Install AgeOS Control Center desktop app"
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

ageos_prompt_desktop_app_install() {
  local selected=0
  local rendered=0
  local key rest
  cat >/dev/tty <<'EOF'

AgeOS installer
We noticed you are using CLI install.
Do you want to install the desktop app as well?
You can always install it later by running: ageos app

Use Up/Down arrows and press Enter.
EOF
  while true; do
    ageos_render_choice_options "$selected" "$rendered"
    rendered=1
    IFS= read -rsn1 key </dev/tty || key=""
    case "$key" in
      "")
        case "$selected" in
          0) echo "1"; return ;;
          1) echo "0"; return ;;
          2) ageos_show_desktop_app_config; rendered=0 ;;
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
        ageos_show_desktop_app_config
        rendered=0
        ;;
    esac
  done
}

ageos_resolve_desktop_app_choice() {
  local explicit normalized choice_file choice
  explicit="${AGEOS_INSTALL_APP:-}"
  if [[ -n "$explicit" ]]; then
    normalized="$(ageos_install_normalize_yes_no "$explicit")"
    if [[ -n "$normalized" ]]; then
      echo "$normalized"
      return
    fi
    echo "Invalid AGEOS_INSTALL_APP value: $explicit (expected yes/no)." >&2
    exit 1
  fi

  choice_file="$(ageos_install_choice_file)"
  if [[ -f "$choice_file" ]]; then
    normalized="$(ageos_install_normalize_yes_no "$(tr -d '[:space:]' < "$choice_file")")"
    if [[ -n "$normalized" ]]; then
      echo "$normalized"
      return
    fi
  fi

  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    echo "0"
    return
  fi

  choice="$(ageos_prompt_desktop_app_install)"
  printf '%s\n' "$choice" > "$choice_file"
  echo "$choice"
}
