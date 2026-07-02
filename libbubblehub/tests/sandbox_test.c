#include "bubblehub/sandbox.h"

#include "test_common.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>

static int test_null_config(void) {
    TEST_CHECK_EQ(bubblehub_sandbox_run(NULL), -EINVAL);
    return 0;
}

static int test_missing_binary(void) {
    char *argv[] = {NULL};
    bubblehub_sandbox_config cfg = {
        .binary = NULL,
        .argv = argv,
        .workdir = "/tmp",
    };
    TEST_CHECK_EQ(bubblehub_sandbox_run(&cfg), -EINVAL);
    return 0;
}

static int test_missing_argv(void) {
    bubblehub_sandbox_config cfg = {
        .binary = "/bin/true",
        .argv = NULL,
        .workdir = "/tmp",
    };
    TEST_CHECK_EQ(bubblehub_sandbox_run(&cfg), -EINVAL);
    return 0;
}

static int test_missing_workdir(void) {
    char *argv[] = {"/bin/true", NULL};
    bubblehub_sandbox_config cfg = {
        .binary = "/bin/true",
        .argv = argv,
        .workdir = NULL,
    };
    TEST_CHECK_EQ(bubblehub_sandbox_run(&cfg), -EINVAL);
    return 0;
}

#ifndef __linux__
static int test_unsupported_platform(void) {
    char *argv[] = {"/bin/true", NULL};
    bubblehub_sandbox_config cfg = {
        .binary = "/bin/true",
        .argv = argv,
        .workdir = "/tmp",
    };
    TEST_CHECK_EQ(bubblehub_sandbox_run(&cfg), -ENOTSUP);
    return 0;
}
#endif

int main(void) {
    int rc = 0;
    rc |= test_null_config();
    rc |= test_missing_binary();
    rc |= test_missing_argv();
    rc |= test_missing_workdir();
#ifndef __linux__
    rc |= test_unsupported_platform();
#endif
    return rc;
}
