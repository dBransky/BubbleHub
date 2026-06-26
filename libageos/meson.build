project('ageos', 'c', version: '0.1.0', default_options: ['warning_level=2', 'c_std=gnu11'])

cc = meson.get_compiler('c')
seccomp_dep = dependency('libseccomp', required: false)

include_dir = include_directories('include')

libageos_sources = [
  'access_policy.c',
  'hw_detect.c',
  'http_proxy.c',
  'limits.c',
  'landlock.c',
  'log.c',
  'overfs.c',
  'scheduler.c',
  'sandbox.c',
]

libageos = shared_library(
  'ageos',
  libageos_sources,
  include_directories: include_dir,
  dependencies: seccomp_dep,
  install: true,
)

executable(
  'ageos-sandbox',
  ['ageos_sandbox_main.c'],
  include_directories: include_dir,
  link_with: libageos,
  install: true,
)

subdir('tests')
