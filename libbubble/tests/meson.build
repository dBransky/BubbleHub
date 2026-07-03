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
