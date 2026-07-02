#include "bubblehub/log.h"

#include "test_common.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int read_file_contents(const char *path, char *buffer, size_t buffer_size) {
    FILE *fp = fopen(path, "r");
    if (fp == NULL) {
        return -1;
    }
    size_t read = fread(buffer, 1, buffer_size - 1, fp);
    buffer[read] = '\0';
    fclose(fp);
    return 0;
}

static int test_log_levels_and_file_output(void) {
    char *dir = test_mkdtemp_copy("bubblehub-log-test");
    TEST_CHECK(dir != NULL);

    char log_path[512];
    snprintf(log_path, sizeof(log_path), "%s/native.log", dir);

    bubblehub_log_set_level("debug");
    bubblehub_log_set_file(log_path);
    bubblehub_log_write(BUBBLEHUB_LOG_LEVEL_INFO, __FILE__, __LINE__, "unit test message", "key=%s", "value");

    char contents[1024];
    TEST_CHECK(read_file_contents(log_path, contents, sizeof(contents)) == 0);
    TEST_CHECK(strstr(contents, "INFO") != NULL);
    TEST_CHECK(strstr(contents, "unit test message") != NULL);
    TEST_CHECK(strstr(contents, "key=value") != NULL);

    bubblehub_log_set_level("error");
    bubblehub_log_write(BUBBLEHUB_LOG_LEVEL_INFO, __FILE__, __LINE__, "filtered info", NULL);
    TEST_CHECK(read_file_contents(log_path, contents, sizeof(contents)) == 0);
    TEST_CHECK(strstr(contents, "filtered info") == NULL);

    bubblehub_log_write(BUBBLEHUB_LOG_LEVEL_ERROR, __FILE__, __LINE__, "visible error", NULL);
    TEST_CHECK(read_file_contents(log_path, contents, sizeof(contents)) == 0);
    TEST_CHECK(strstr(contents, "visible error") != NULL);

    free(dir);
    return 0;
}

static int test_log_init_from_env(void) {
    setenv("BUBBLEHUB_LOG_LEVEL", "debug", 1);
    bubblehub_log_init();
    bubblehub_log_set_file(NULL);
    bubblehub_log_write(BUBBLEHUB_LOG_LEVEL_DEBUG, __FILE__, __LINE__, "debug after init", NULL);
    unsetenv("BUBBLEHUB_LOG_LEVEL");
    return 0;
}

static int test_sandbox_log_file_restriction(void) {
    char *dir = test_mkdtemp_copy("bubblehub-log-sandbox-test");
    TEST_CHECK(dir != NULL);

    char allowed_path[512];
    snprintf(allowed_path, sizeof(allowed_path), "%s/allowed.log", dir);

    setenv("BUBBLEHUB_SANDBOX", "1", 1);
    setenv("BUBBLEHUB_WORKSPACE", dir, 1);
    bubblehub_log_set_file(allowed_path);
    bubblehub_log_write(BUBBLEHUB_LOG_LEVEL_ERROR, __FILE__, __LINE__, "sandbox allowed log", NULL);

    char contents[256];
    TEST_CHECK(read_file_contents(allowed_path, contents, sizeof(contents)) == 0);
    TEST_CHECK(strstr(contents, "sandbox allowed log") != NULL);

    bubblehub_log_set_file("/etc/passwd");
    bubblehub_log_write(BUBBLEHUB_LOG_LEVEL_ERROR, __FILE__, __LINE__, "blocked path", NULL);
    FILE *blocked = fopen("/etc/passwd", "r");
    if (blocked != NULL) {
        char blocked_contents[4096];
        size_t read = fread(blocked_contents, 1, sizeof(blocked_contents) - 1, blocked);
        blocked_contents[read] = '\0';
        fclose(blocked);
        TEST_CHECK(strstr(blocked_contents, "blocked path") == NULL);
    }

    unsetenv("BUBBLEHUB_SANDBOX");
    unsetenv("BUBBLEHUB_WORKSPACE");
    free(dir);
    return 0;
}

int main(void) {
    int rc = 0;
    rc |= test_log_levels_and_file_output();
    rc |= test_log_init_from_env();
    rc |= test_sandbox_log_file_restriction();
    return rc;
}
