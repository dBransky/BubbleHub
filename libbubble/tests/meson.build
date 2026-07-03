libbubble_dep = declare_dependency(
  link_with: libbubble,
  include_directories: include_dir,
)

test_rpath = meson.current_build_dir() / '..'

test_programs = [
  'access_policy',
  'http_proxy',
  'log',
  'hw',
  'limits',
  'overfs',
  'landlock',
  'scheduler',
  'sandbox',
]

foreach name : test_programs
  exe = executable(
    'bubble-' + name + '-test',
    name + '_test.c',
    dependencies: libbubble_dep,
    build_rpath: test_rpath,
    install: false,
  )
  parallel = true
  if name == 'http_proxy'
    parallel = false
  endif
  test(name, exe, is_parallel: parallel)
endforeach

sandbox_internal_exe = executable(
  'bubble-sandbox-internal-test',
  'sandbox_internal_test.c',
  include_directories: include_dir,
  dependencies: seccomp_dep,
  install: false,
)
test('sandbox_internal', sandbox_internal_exe)

scheduler_internal_exe = executable(
  'bubble-scheduler-internal-test',
  'scheduler_internal_test.c',
  include_directories: include_dir,
  install: false,
)
test('scheduler_internal', scheduler_internal_exe)

landlock_internal_exe = executable(
  'bubble-landlock-internal-test',
  'landlock_internal_test.c',
  include_directories: include_dir,
  install: false,
)
test('landlock_internal', landlock_internal_exe)

http_proxy_internal_exe = executable(
  'bubble-http-proxy-internal-test',
  'http_proxy_internal_test.c',
  include_directories: include_dir,
  install: false,
)
test('http_proxy_internal', http_proxy_internal_exe)

overfs_internal_exe = executable(
  'bubble-overfs-internal-test',
  'overfs_internal_test.c',
  include_directories: include_dir,
  install: false,
)
test('overfs_internal', overfs_internal_exe)

access_policy_internal_exe = executable(
  'bubble-access-policy-internal-test',
  'access_policy_internal_test.c',
  include_directories: include_dir,
  install: false,
)
test('access_policy_internal', access_policy_internal_exe)
