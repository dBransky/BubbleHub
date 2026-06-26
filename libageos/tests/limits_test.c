#include "ageos/limits.h"
#include "ageos/sandbox.h"

#include "test_common.h"

#include <errno.h>
#include <stdio.h>

static int test_null_config_rejected(void) {
    TEST_CHECK_EQ(ageos_apply_cgroup_limits(NULL), -EINVAL);
    return 0;
}

static int test_apply_limits_best_effort(void) {
    ageos_sandbox_config cfg = {
        .memory_max = 512ULL * 1024ULL * 1024ULL,
        .cpu_percent = 50,
    };
    TEST_CHECK(ageos_apply_cgroup_limits(&cfg) == 0);
    return 0;
}

static int test_zero_limits_accepted(void) {
    ageos_sandbox_config cfg = {0};
    TEST_CHECK(ageos_apply_cgroup_limits(&cfg) == 0);
    return 0;
}

int main(void) {
    int rc = 0;
    rc |= test_null_config_rejected();
    rc |= test_apply_limits_best_effort();
    rc |= test_zero_limits_accepted();
    return rc;
}
