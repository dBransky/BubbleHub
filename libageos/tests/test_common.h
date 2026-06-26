#pragma once

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static inline int test_fail_line(const char *file, int line, const char *msg) {
    fprintf(stderr, "FAIL %s:%d: %s\n", file, line, msg);
    return 1;
}

#define TEST_CHECK(cond)                                      \
    do {                                                      \
        if (!(cond)) {                                        \
            return test_fail_line(__FILE__, __LINE__, #cond); \
        }                                                     \
    } while (0)

#define TEST_CHECK_EQ(a, b)                                                                                         \
    do {                                                                                                            \
        typeof(a) _a = (a);                                                                                         \
        typeof(b) _b = (b);                                                                                         \
        if (_a != _b) {                                                                                             \
            fprintf(stderr, "FAIL %s:%d: %s != %s (%ld != %ld)\n", __FILE__, __LINE__, #a, #b, (long)_a, (long)_b); \
            return 1;                                                                                               \
        }                                                                                                           \
    } while (0)

#define TEST_CHECK_STR(a, b)                                       \
    do {                                                           \
        const char *_a = (a);                                      \
        const char *_b = (b);                                      \
        if (_a == NULL || _b == NULL || strcmp(_a, _b) != 0) {     \
            fprintf(                                               \
                stderr,                                            \
                "FAIL %s:%d: strcmp(%s, %s) got '%s' want '%s'\n", \
                __FILE__,                                          \
                __LINE__,                                          \
                #a,                                                \
                #b,                                                \
                _a != NULL ? _a : "<null>",                        \
                _b != NULL ? _b : "<null>");                       \
            return 1;                                              \
        }                                                          \
    } while (0)

#define TEST_CHECK_CONTAINS(haystack, needle)                                             \
    do {                                                                                  \
        const char *_haystack = (haystack);                                               \
        const char *_needle = (needle);                                                   \
        if (_haystack == NULL || _needle == NULL || strstr(_haystack, _needle) == NULL) { \
            fprintf(                                                                      \
                stderr,                                                                   \
                "FAIL %s:%d: '%s' not found in '%s'\n",                                   \
                __FILE__,                                                                 \
                __LINE__,                                                                 \
                _needle != NULL ? _needle : "<null>",                                     \
                _haystack != NULL ? _haystack : "<null>");                                \
            return 1;                                                                     \
        }                                                                                 \
    } while (0)

static inline char *test_mkdtemp_copy(const char *prefix) {
    char template[256];
    snprintf(template, sizeof(template), "/tmp/%s-XXXXXX", prefix);
    char *dir = mkdtemp(template);
    if (dir == NULL) {
        return NULL;
    }
    return strdup(dir);
}
