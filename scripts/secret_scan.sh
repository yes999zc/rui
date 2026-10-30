#!/usr/bin/env bash
# Scan tracked files for credential-shaped strings.
#
# A public-repo failure mode that is cheap to prevent: one pasted key, one absolute
# laptop path or one LAN address in a commit and the value is in the history forever.
# Scans tracked files only (git grep against the index/worktree).
#
# Called from two places so the same rules decide both questions:
#   - .github/workflows/secret-scan.yml  (every push and PR; its own red check name)
#   - .github/workflows/docker-publish.yml (the `test` gate, so a leak blocks the image
#     instead of publishing next to a red scan — which is what happened for 0.8.12)
# Keep the regexes and exclude paths here and nowhere else; a copy in a workflow drifts.
set -u

# Two classes, because they carry different risk:
#  - hard: something that authenticates (an sk- key, a bearer token, a
#    literal assigned to a passw[o]rd field), or an absolute home path
#    that fingerprints this machine.
#  - soft: a raw RFC1918 address. Only telling in aggregate (LAN topology),
#    and netguard's own tests must use private ranges to prove
#    private-address blocking works, so tests/ is exempt from this class
#    and from nothing else.
#
# The passw[o]rd rule matches assignments to a *literal* only: a quoted
# value, or an unquoted token that looks like a value (contains a digit).
# Annotations (`passw[o]rd: str`), pass-throughs
# (`passw[o]rd=passw[o]rd`), lookups (`data.get("passw[o]rd")`),
# comparisons (`cfg.music.passw[o]rd == SECRET`) and shell references
# (`passw[o]rd: $VAR`) are not credentials; matching them made every
# annotation a permanent red check.
#
# Neither class matches a CIDR such as 192.168.0.0/24. A documented policy
# example is not a host address; flagging it made every netguard doc a
# permanent failure. The regexes spell the sensitive words with a character
# class so this file does not match the scan itself.
hard="sk-[A-Za-z0-9]{16,}|[/]Users[/]|passw[o]rd[[:space:]]*[:=][[:space:]]*[\"'][^\"']+[\"']|passw[o]rd[[:space:]]*[:=][[:space:]]*[^\"'\$[:space:]]*[0-9][^\"'\$[:space:]]*|Bearer [A-Za-z0-9_-]{20,}"
soft='(^|[^0-9.])192\.168\.[0-9]{1,3}\.[0-9]{1,3}([^0-9/]|$)|(^|[^0-9.])10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}([^0-9/]|$)'

# Redacted samples written up by an audit, and the binary fixtures, are
# intentionally fake. Everything else must be reviewed rather than excluded
# by directory, so a real leak in docs/ or src/ is still found. Findings are
# printed verbatim: fix or add one line of reviewed evidence, never a
# directory-wide exclusion for the hard class.
git grep -nIE "$hard" -- . \
  ':(exclude)docs/*audit*' \
  ':(exclude)tests/fixtures/*' > findings.txt || true
git grep -nIE "$soft" -- . \
  ':(exclude)docs/*audit*' \
  ':(exclude)tests/fixtures/*' \
  ':(exclude)tests/*' >> findings.txt || true

if [ -s findings.txt ]; then
  echo "::error::possible credential material in tracked files (see below)"
  cat findings.txt
  rm -f findings.txt
  exit 1
fi
rm -f findings.txt
echo "secret-scan: clean"
