#define _GNU_SOURCE

#include "test_common.h"

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "../landlock.c"

static int test_right_sets_by_abi(void) {
    uint64_t read = read_rights();
    TEST_CHECK(read != 0);
    TEST_CHECK((write_rights(1) & LANDLOCK_ACCESS_FS_REFER) == 0);
    TEST_CHECK(write_rights(2) & LANDLOCK_ACCESS_FS_REFER);
    TEST_CHECK(write_rights(3) & LANDLOCK_ACCESS_FS_TRUNCATE);
    TEST_CHECK((handled_rights(3) & read) == read);
    TEST_CHECK(device_rights(1) == (LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_WRITE_FILE));
    TEST_CHECK((state_file_rights(2) & LANDLOCK_ACCESS_FS_TRUNCATE) == 0);
    TEST_CHECK(state_file_rights(3) & LANDLOCK_ACCESS_FS_TRUNCATE);
    TEST_CHECK(readonly_file_rights() == LANDLOCK_ACCESS_FS_READ_FILE);
    return 0;
}

static int test_path_rule_helpers_handle_empty_missing_and_bad_ruleset(void) {
    char *dir = test_mkdtemp_copy("bubblehub-landlock-internal");
    TEST_CHECK(dir != NULL);
    char file_path[512];
    snprintf(file_path, sizeof(file_path), "%s/file.txt", dir);
    FILE *fp = fopen(file_path, "w");
    TEST_CHECK(fp != NULL);
    fputs("data", fp);
    fclose(fp);

    TEST_CHECK(add_path_rule(-1, NULL, read_rights()) == 0);
    TEST_CHECK(add_path_rule(-1, "", read_rights()) == 0);
    TEST_CHECK(add_path_rule(-1, "/tmp/bubblehub-landlock-missing", read_rights()) == 0);
    TEST_CHECK(add_path_rule(-1, file_path, read_rights()) == -EBADF);

    const char *paths[] = {"", "/tmp/bubblehub-landlock-missing"};
    TEST_CHECK(add_path_rules(-1, paths, 2, read_rights()) == 0);
    const char *bad_paths[] = {file_path};
    TEST_CHECK(add_path_rules(-1, bad_paths, 1, read_rights()) == -EBADF);
    free(dir);
    return 0;
}

static int test_environment_rule_helpers(void) {
    char *dir = test_mkdtemp_copy("bubblehub-landlock-env");
    TEST_CHECK(dir != NULL);
    char file_path[512];
    snprintf(file_path, sizeof(file_path), "%s/models.yaml", dir);
    FILE *fp = fopen(file_path, "w");
    TEST_CHECK(fp != NULL);
    fputs("models: []\n", fp);
    fclose(fp);

    unsetenv("HOME");
    unsetenv("BUBBLEHUB_CACHE");
    unsetenv("BUBBLEHUB_SCHEDULER_STATE");
    unsetenv("BUBBLEHUB_MODELS_CONFIG");
    unsetenv("BUBBLEHUB_ROOTFS_DIR");
    TEST_CHECK(add_home_child_rule(-1, ".cache/bubblehub", read_rights()) == 0);
    TEST_CHECK(add_env_or_home_cache_rule(-1) == 0);
    TEST_CHECK(add_scheduler_state_rule(-1, 3) == 0);
    TEST_CHECK(add_models_config_rule(-1) == 0);
    TEST_CHECK(add_rootfs_rule(-1) == 0);
    TEST_CHECK(add_sandbox_home_rule(-1, 3) == 0);

    setenv("HOME", dir, 1);
    setenv("BUBBLEHUB_CACHE", dir, 1);
    setenv("BUBBLEHUB_SCHEDULER_STATE", file_path, 1);
    setenv("BUBBLEHUB_MODELS_CONFIG", file_path, 1);
    setenv("BUBBLEHUB_ROOTFS_DIR", dir, 1);
    TEST_CHECK(add_home_child_rule(-1, ".cache/bubblehub", read_rights()) == 0);
    TEST_CHECK(add_env_or_home_cache_rule(-1) == -EBADF);
    TEST_CHECK(add_scheduler_state_rule(-1, 3) == -EBADF);
    TEST_CHECK(add_models_config_rule(-1) == -EBADF);
    TEST_CHECK(add_rootfs_rule(-1) == -EBADF);
    TEST_CHECK(add_identity_file_rules(-1) == -EBADF);
    TEST_CHECK(add_sandbox_home_rule(-1, 3) == -EBADF);
    free(dir);
    return 0;
}

static int test_writable_dir_validation(void) {
    char *dir = test_mkdtemp_copy("bubblehub-landlock-writable");
    TEST_CHECK(dir != NULL);
    TEST_CHECK(path_is_beneath("/tmp/child", "/tmp"));
    TEST_CHECK(path_is_beneath("/tmp", "/tmp"));
    TEST_CHECK(!path_is_beneath("/tmp/child", "/"));
    TEST_CHECK(validate_writable_dir(dir) == 0);
    TEST_CHECK(validate_writable_dir("/usr") == -EPERM);
    TEST_CHECK(validate_writable_dir("/tmp/bubblehub-landlock-not-present") == -ENOENT);
    free(dir);
    return 0;
}

int main(void) {
    int rc = 0;
    rc |= test_right_sets_by_abi();
    rc |= test_path_rule_helpers_handle_empty_missing_and_bad_ruleset();
    rc |= test_environment_rule_helpers();
    rc |= test_writable_dir_validation();
    return rc;
}
