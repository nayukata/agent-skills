#!/usr/bin/env bash
set -euo pipefail

# Installs skills/ into a shared hub (~/.agents/skills) and symlinks each
# skill into per-agent skills dirs (~/.claude/skills, ~/.codex/skills, ...).
# Works on bash 3.2 (macOS default); avoids bash-4-only features.

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$REPO_DIR/skills"
HUB_DIR="$HOME/.agents/skills"

COPY_MODE=0
AGENT_DIRS="$HOME/.claude/skills $HOME/.codex/skills $HOME/.gemini/skills"

usage() {
  cat <<'EOF'
Usage: ./install.sh [--copy] [--agent-dir <path>]...

Options:
  --copy                Copy skills instead of symlinking (for sandboxes
                         that cannot follow symlinks).
  --agent-dir <path>    Additional agent skills directory to install into.
                         Can be given multiple times.
  -h, --help            Show this help.

Default agent dirs: ~/.claude/skills, ~/.codex/skills, ~/.gemini/skills
(only installed into if the parent dir, e.g. ~/.claude, already exists)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --copy)
      COPY_MODE=1
      shift
      ;;
    --agent-dir)
      if [ $# -lt 2 ]; then
        echo "error: --agent-dir requires a path" >&2
        exit 1
      fi
      AGENT_DIRS="$AGENT_DIRS $2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [ ! -d "$SKILLS_DIR" ]; then
  echo "error: $SKILLS_DIR not found" >&2
  exit 1
fi

SKILL_NAMES=""
for dir in "$SKILLS_DIR"/*/; do
  name="$(basename "$dir")"
  if [ -f "$dir/SKILL.md" ]; then
    SKILL_NAMES="$SKILL_NAMES $name"
  fi
done

if [ -z "$SKILL_NAMES" ]; then
  echo "error: no skills with SKILL.md found under $SKILLS_DIR" >&2
  exit 1
fi

LINKED=""
COPIED=""
SKIPPED=""

# install_one <target_dir> <name> <source_path>
install_one() {
  target_dir="$1"
  name="$2"
  src="$3"
  dest="$target_dir/$name"

  if [ -e "$dest" ] || [ -L "$dest" ]; then
    if [ -L "$dest" ]; then
      rm "$dest"
    else
      echo "skip: $name (real directory exists at $dest)" >&2
      SKIPPED="$SKIPPED $dest"
      return
    fi
  fi

  if [ "$COPY_MODE" -eq 1 ]; then
    cp -R "$src" "$dest"
    COPIED="$COPIED $dest"
  else
    ln -s "$src" "$dest"
    LINKED="$LINKED $dest"
  fi
}

mkdir -p "$HUB_DIR"

for name in $SKILL_NAMES; do
  install_one "$HUB_DIR" "$name" "$SKILLS_DIR/$name"
done

for agent_dir in $AGENT_DIRS; do
  parent_dir="$(dirname "$agent_dir")"
  if [ ! -d "$parent_dir" ]; then
    continue
  fi
  mkdir -p "$agent_dir"
  for name in $SKILL_NAMES; do
    hub_entry="$HUB_DIR/$name"
    if [ ! -e "$hub_entry" ]; then
      continue
    fi
    install_one "$agent_dir" "$name" "$hub_entry"
  done
done

count() {
  # shellcheck disable=SC2086
  set -- $1
  echo "$#"
}

echo ""
echo "Summary:"
echo "  linked:  $(count "$LINKED")"
for p in $LINKED; do echo "    $p"; done
echo "  copied:  $(count "$COPIED")"
for p in $COPIED; do echo "    $p"; done
echo "  skipped: $(count "$SKIPPED")"
for p in $SKIPPED; do echo "    $p"; done
