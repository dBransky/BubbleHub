#pragma once

#include "ageos/sandbox.h"

#include <stddef.h>
#include <sys/types.h>

int ageos_overfs_rootfs_enabled(const ageos_sandbox_config *cfg);
int ageos_overfs_setup_mounts(const char *new_root, const ageos_sandbox_config *cfg);
int ageos_overfs_join_mount_path(const char *root, const char *path, char *buffer, size_t buffer_size);
int ageos_overfs_mkdir_p(const char *path, mode_t mode);
int ageos_overfs_ensure_file(const char *path, mode_t mode);
int ageos_overfs_bind_file_readonly(const char *source, const char *target);
int ageos_overfs_bind_file_readwrite(const char *source, const char *target);
int ageos_overfs_bind_dir(const char *source, const char *target);
int ageos_overfs_bind_optional_dir_readonly(const char *source, const char *target);
int ageos_overfs_bind_optional_file_readonly(const char *source, const char *target);
int ageos_overfs_mount_tmpfs_at(const char *target, const char *options);
