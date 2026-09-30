#!/usr/bin/env bash
# Disposable loopback PostgreSQL for the single-UID Work environment only.
set -euo pipefail
cd "$(dirname "$0")/.."
pg_bin_dir="${TITAN_TEST_PG_BIN_DIR:-/tmp/titan-pgbin/usr/lib/postgresql/16/bin}"
pg_lib_dir="${TITAN_TEST_PG_LIB_DIR:-/tmp/titan-pgbin/usr/lib/x86_64-linux-gnu}"
[[ -x "$pg_bin_dir/initdb" && -x "$pg_bin_dir/pg_ctl" ]]
pg_test_dir="$(mktemp -d /tmp/titan-pgdata-XXXXXX)"
pg_shim_dir="$(mktemp -d /tmp/titan-pgshim-XXXXXX)"
cleanup(){
 LD_PRELOAD="$pg_shim_dir/uid.so" LD_LIBRARY_PATH="$pg_lib_dir" "$pg_bin_dir/pg_ctl" -D "$pg_test_dir" stop >/dev/null 2>&1 || true
 rm -rf "$pg_test_dir" "$pg_shim_dir"
}
trap cleanup EXIT
gcc -shared -fPIC -o "$pg_shim_dir/uid.so" test/work-test-uid.c -ldl
LD_PRELOAD="$pg_shim_dir/uid.so" LD_LIBRARY_PATH="$pg_lib_dir" "$pg_bin_dir/initdb" -D "$pg_test_dir" -U titan_test -A trust --no-locale -E UTF8 >/dev/null
LD_PRELOAD="$pg_shim_dir/uid.so" LD_LIBRARY_PATH="$pg_lib_dir" "$pg_bin_dir/pg_ctl" -D "$pg_test_dir" -l "$pg_test_dir/test.log" -o "-h 127.0.0.1 -p 55439 -c unix_socket_directories=''" start
TITAN_TEST_DATABASE_URL=postgresql://titan_test@127.0.0.1:55439/postgres npm test
