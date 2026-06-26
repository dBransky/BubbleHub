#include "ageos/log.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

static ageos_log_level_t g_log_level = AGEOS_LOG_LEVEL_ERROR;
static FILE *g_log_sink = NULL;
static int g_log_sink_owned = 0;
static int g_log_initialized = 0;

static ageos_log_level_t parse_level(const char *value) {
    if (value == NULL || value[0] == '\0') {
        return AGEOS_LOG_LEVEL_ERROR;
    }
    if (strcasecmp(value, "debug") == 0) {
        return AGEOS_LOG_LEVEL_DEBUG;
    }
    if (strcasecmp(value, "info") == 0) {
        return AGEOS_LOG_LEVEL_INFO;
    }
    return AGEOS_LOG_LEVEL_ERROR;
}

static const char *basename_path(const char *path) {
    const char *slash = strrchr(path, '/');
    return slash != NULL ? slash + 1 : path;
}

static const char *level_name(int level) {
    switch (level) {
        case AGEOS_LOG_LEVEL_INFO:
            return "INFO";
        case AGEOS_LOG_LEVEL_DEBUG:
            return "DEBUG";
        default:
            return "ERROR";
    }
}

static void close_log_sink(void) {
    if (g_log_sink_owned && g_log_sink != NULL) {
        fclose(g_log_sink);
    }
    g_log_sink = NULL;
    g_log_sink_owned = 0;
}

static void configure_log_file(const char *path) {
    close_log_sink();
    if (path == NULL || path[0] == '\0') {
        return;
    }
    g_log_sink = fopen(path, "a");
    if (g_log_sink != NULL) {
        g_log_sink_owned = 1;
    }
}

static FILE *log_sink(void) {
    return g_log_sink != NULL ? g_log_sink : stderr;
}

static int path_starts_with(const char *path, const char *prefix) {
    size_t prefix_len;
    if (path == NULL || prefix == NULL || prefix[0] == '\0') {
        return 0;
    }
    prefix_len = strlen(prefix);
    if (strncmp(path, prefix, prefix_len) != 0) {
        return 0;
    }
    return path[prefix_len] == '\0' || path[prefix_len] == '/';
}

static int sandbox_allows_log_file(const char *path) {
    static const char *const env_roots[] = {
        "AGEOS_AGENT_HOME",
        "AGEOS_WORKSPACE",
        "TMPDIR",
        "HOME",
    };
    size_t i;

    if (getenv("AGEOS_SANDBOX") == NULL || strcmp(getenv("AGEOS_SANDBOX"), "1") != 0) {
        return 1;
    }
    if (path == NULL || path[0] == '\0') {
        return 0;
    }
    for (i = 0; i < sizeof(env_roots) / sizeof(env_roots[0]); i++) {
        const char *root = getenv(env_roots[i]);
        if (root != NULL && path_starts_with(path, root)) {
            return 1;
        }
    }
    return path_starts_with(path, "/workspace");
}

static int should_emit(int level) {
    if (!g_log_initialized) {
        ageos_log_init();
    }
    if (level == AGEOS_LOG_LEVEL_ERROR) {
        return 1;
    }
    return (int)g_log_level >= level;
}

void ageos_log_init(void) {
    g_log_level = parse_level(getenv("AGEOS_LOG_LEVEL"));
    g_log_initialized = 1;
}

void ageos_log_set_level(const char *level) {
    g_log_level = parse_level(level);
    g_log_initialized = 1;
}

void ageos_log_set_file(const char *path) {
    if (path != NULL && path[0] != '\0' && !sandbox_allows_log_file(path)) {
        close_log_sink();
        g_log_initialized = 1;
        return;
    }
    configure_log_file(path);
    g_log_initialized = 1;
}

void ageos_log_write(int level, const char *file, int line, const char *text, const char *fmt, ...) {
    FILE *sink;
    if (!should_emit(level)) {
        return;
    }
    sink = log_sink();
    fprintf(sink, "%s %s:%d %s", level_name(level), basename_path(file), line, text);
    if (fmt != NULL && fmt[0] != '\0') {
        fprintf(sink, ":");
        va_list args;
        va_start(args, fmt);
        vfprintf(sink, fmt, args);
        va_end(args);
    }
    fprintf(sink, "\n");
    fflush(sink);
}
