#include "test_common.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifdef __linux__
#include <sys/wait.h>

extern int bubblehub_landlock_apply_filesystem(const char *writable_dir, int allow_dns);
#endif

static int test_unsupported_platform(void) {
#ifndef __linux__
    extern int bubblehub_landlock_apply_filesystem(const char *writable_dir, int allow_dns);
    (void)bubblehub_landlock_apply_filesystem;
#endif
    return 0;
}

#ifdef __linux__
static int test_rejects_protected_writable_dir(void) {
    TEST_CHECK(bubblehub_landlock_apply_filesystem("/usr", 0) == -EPERM);
    TEST_CHECK(bubblehub_landlock_apply_filesystem("/etc", 0) == -EPERM);
    return 0;
}

static int test_applies_to_writable_temp_dir_in_child(void) {
    char *dir = test_mkdtemp_copy("bubblehub-landlock-test");
    TEST_CHECK(dir != NULL);

    pid_t pid = fork();
    TEST_CHECK(pid >= 0);
    if (pid == 0) {
        int rc = bubblehub_landlock_apply_filesystem(dir, 1);
        _exit(rc == 0 ? 0 : 1);
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
    rc |= test_unsupported_platform();
#ifdef __linux__
    rc |= test_rejects_protected_writable_dir();
    rc |= test_applies_to_writable_temp_dir_in_child();
#endif
    return rc;
}
