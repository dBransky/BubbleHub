#pragma once

#include "bubblehub/sandbox.h"

#include <stddef.h>
#include <sys/types.h>

int bubblehub_overfs_rootfs_enabled(const bubblehub_sandbox_config *cfg);
int bubblehub_overfs_setup_mounts(const char *new_root, const bubblehub_sandbox_config *cfg);
int bubblehub_overfs_join_mount_path(const char *root, const char *path, char *buffer, size_t buffer_size);
int bubblehub_overfs_mkdir_p(const char *path, mode_t mode);
int bubblehub_overfs_ensure_file(const char *path, mode_t mode);
int bubblehub_overfs_bind_file_readonly(const char *source, const char *target);
int bubblehub_overfs_bind_file_readwrite(const char *source, const char *target);
int bubblehub_overfs_bind_dir(const char *source, const char *target);
int bubblehub_overfs_bind_optional_dir_readonly(const char *source, const char *target);
int bubblehub_overfs_bind_optional_file_readonly(const char *source, const char *target);
int bubblehub_overfs_mount_tmpfs_at(const char *target, const char *options);
