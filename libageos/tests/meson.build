libageos_dep = declare_dependency(
  link_with: libageos,
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
    'ageos-' + name + '-test',
    name + '_test.c',
    dependencies: libageos_dep,
    build_rpath: test_rpath,
    install: false,
  )
  test(name, exe)
endforeach
