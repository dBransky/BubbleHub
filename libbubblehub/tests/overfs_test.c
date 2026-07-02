#include "bubblehub/overfs.h"
#include "bubblehub/sandbox.h"

#include "test_common.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifdef __linux__
#include <sys/mount.h>
#include <sys/wait.h>
#endif

static int test_rootfs_enabled(void) {
    bubblehub_sandbox_config empty = {0};
    TEST_CHECK(!bubblehub_overfs_rootfs_enabled(&empty));

    bubblehub_sandbox_config enabled = {
        .rootfs_dir = "/tmp/rootfs",
    };
    TEST_CHECK(bubblehub_overfs_rootfs_enabled(&enabled));
    return 0;
}

static int test_join_mount_path(void) {
    char buffer[256];

    TEST_CHECK_EQ(bubblehub_overfs_join_mount_path(NULL, "/tmp/x", buffer, sizeof(buffer)), 0);
    TEST_CHECK_STR(buffer, "/tmp/x");

    TEST_CHECK_EQ(bubblehub_overfs_join_mount_path("/newroot", "/etc/passwd", buffer, sizeof(buffer)), 0);
    TEST_CHECK_STR(buffer, "/newroot/etc/passwd");

    TEST_CHECK_EQ(bubblehub_overfs_join_mount_path("/newroot", "relative", buffer, sizeof(buffer)), -EINVAL);
    return 0;
}

static int test_mkdir_p_and_ensure_file(void) {
    char *dir = test_mkdtemp_copy("bubblehub-overfs-path-test");
    TEST_CHECK(dir != NULL);

    char nested[512];
    snprintf(nested, sizeof(nested), "%s/a/b/c", dir);
    TEST_CHECK(bubblehub_overfs_mkdir_p(nested, 0755) == 0);

    char file_path[512];
    snprintf(file_path, sizeof(file_path), "%s/a/b/test.file", dir);
    TEST_CHECK(bubblehub_overfs_ensure_file(file_path, 0644) == 0);
    TEST_CHECK(bubblehub_overfs_ensure_file(file_path, 0644) == 0);

    struct stat st;
    TEST_CHECK(stat(file_path, &st) == 0);
    TEST_CHECK(S_ISREG(st.st_mode));

    TEST_CHECK(bubblehub_overfs_mkdir_p(NULL, 0755) == -EINVAL);
    free(dir);
    return 0;
}

static int test_optional_binds_skip_missing(void) {
    char *dir = test_mkdtemp_copy("bubblehub-overfs-optional-test");
    TEST_CHECK(dir != NULL);

    char target_dir[512];
    char target_file[512];
    snprintf(target_dir, sizeof(target_dir), "%s/missing-dir", dir);
    snprintf(target_file, sizeof(target_file), "%s/missing-file", dir);

    TEST_CHECK(bubblehub_overfs_bind_optional_dir_readonly("/tmp/bubblehub-overfs-does-not-exist-dir", target_dir) == 0);
    TEST_CHECK(bubblehub_overfs_bind_optional_file_readonly("/tmp/bubblehub-overfs-does-not-exist-file", target_file) == 0);
    free(dir);
    return 0;
}

#ifdef __linux__
static int mount_helpers_available(void) {
    char *dir = test_mkdtemp_copy("bubblehub-overfs-cap-test");
    if (dir == NULL) {
        return 0;
    }
    char target[512];
    snprintf(target, sizeof(target), "%s/cap", dir);
    int rc = bubblehub_overfs_mount_tmpfs_at(target, "size=1m");
    free(dir);
    return rc == 0;
}

static int test_mount_helpers_in_child(void) {
    if (!mount_helpers_available()) {
        return 0;
    }
    char *dir = test_mkdtemp_copy("bubblehub-overfs-mount-test");
    TEST_CHECK(dir != NULL);

    pid_t pid = fork();
    TEST_CHECK(pid >= 0);
    if (pid == 0) {
        char tmpfs_target[512];
        snprintf(tmpfs_target, sizeof(tmpfs_target), "%s/tmpfs", dir);
        if (bubblehub_overfs_mount_tmpfs_at(tmpfs_target, "mode=1777,size=16m") != 0) {
            _exit(10);
        }

        char source_file[512];
        char target_file[512];
        snprintf(source_file, sizeof(source_file), "%s/source.txt", dir);
        snprintf(target_file, sizeof(target_file), "%s/target.txt", dir);
        FILE *fp = fopen(source_file, "w");
        if (fp == NULL) {
            _exit(11);
        }
        fputs("bind-test", fp);
        fclose(fp);
        if (bubblehub_overfs_ensure_file(target_file, 0644) != 0) {
            _exit(12);
        }
        if (bubblehub_overfs_bind_file_readonly(source_file, target_file) != 0) {
            _exit(13);
        }

        char source_dir[512];
        char target_dir[512];
        snprintf(source_dir, sizeof(source_dir), "%s/source-dir", dir);
        snprintf(target_dir, sizeof(target_dir), "%s/target-dir", dir);
        if (bubblehub_overfs_mkdir_p(source_dir, 0755) != 0 || bubblehub_overfs_mkdir_p(target_dir, 0755) != 0) {
            _exit(14);
        }
        if (bubblehub_overfs_bind_dir(source_dir, target_dir) != 0) {
            _exit(15);
        }
        _exit(0);
    }

    int status = 0;
    waitpid(pid, &status, 0);
    free(dir);
    TEST_CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    return 0;
}

static int test_setup_mounts_without_rootfs(void) {
    if (!mount_helpers_available()) {
        return 0;
    }
    char *dir = test_mkdtemp_copy("bubblehub-overfs-setup-test");
    TEST_CHECK(dir != NULL);

    pid_t pid = fork();
    TEST_CHECK(pid >= 0);
    if (pid == 0) {
        bubblehub_sandbox_config cfg = {0};
        int rc = bubblehub_overfs_setup_mounts(dir, &cfg);
        _exit(rc == 0 ? 0 : 20);
    }

    int status = 0;
    waitpid(pid, &status, 0);
    free(dir);
    TEST_CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    return 0;
}
#endif

int main(void) {
    int rc = 0;
    rc |= test_rootfs_enabled();
    rc |= test_join_mount_path();
    rc |= test_mkdir_p_and_ensure_file();
    rc |= test_optional_binds_skip_missing();
#ifdef __linux__
    rc |= test_mount_helpers_in_child();
    rc |= test_setup_mounts_without_rootfs();
#endif
    return rc;
}
