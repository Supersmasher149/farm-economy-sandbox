#include "mem0_client.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef FARM_WITH_MEM0

#include <curl/curl.h>

#include "cJSON.h"

#define MEM0_API_URL "https://api.mem0.ai/v1/memories/"

static void set_error(char *error_out, size_t error_out_len, const char *msg) {
    if (error_out == NULL || error_out_len == 0) return;
    snprintf(error_out, error_out_len, "%s", msg);
}

bool mem0_client_configured(void) {
    const char *key = getenv("MEM0_API_KEY");
    return key != NULL && key[0] != '\0';
}

typedef struct {
    char *data;
    size_t len;
} Mem0ResponseBuffer;

/* libcurl write callback: appends each chunk and keeps the buffer
 * NUL-terminated so it can double as an error-message source. Returning a
 * short count (via the realloc failure path) is libcurl's documented way
 * to abort the transfer on our own allocation failure. */
static size_t mem0_write_cb(char *ptr, size_t size, size_t nmemb, void *userdata) {
    Mem0ResponseBuffer *buf = userdata;
    size_t add = size * nmemb;
    char *grown = realloc(buf->data, buf->len + add + 1);
    if (grown == NULL) return 0;
    buf->data = grown;
    memcpy(buf->data + buf->len, ptr, add);
    buf->len += add;
    buf->data[buf->len] = '\0';
    return add;
}

bool mem0_add_memory(const char *text, const char *user_id, const char *agent_id,
                     char *error_out, size_t error_out_len) {
    if (text == NULL || user_id == NULL) {
        set_error(error_out, error_out_len, "mem0_add_memory: text and user_id are required");
        return false;
    }
    const char *api_key = getenv("MEM0_API_KEY");
    if (api_key == NULL || api_key[0] == '\0') {
        set_error(error_out, error_out_len,
                  "MEM0_API_KEY is not set -- export it to record memories");
        return false;
    }

    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        set_error(error_out, error_out_len, "mem0_add_memory: failed to build request body");
        return false;
    }
    cJSON *messages = cJSON_AddArrayToObject(root, "messages");
    cJSON *message = cJSON_CreateObject();
    if (messages == NULL || message == NULL) {
        cJSON_Delete(root);
        cJSON_Delete(message);
        set_error(error_out, error_out_len, "mem0_add_memory: failed to build request body");
        return false;
    }
    cJSON_AddStringToObject(message, "role", "user");
    cJSON_AddStringToObject(message, "content", text);
    cJSON_AddItemToArray(messages, message);
    cJSON_AddStringToObject(root, "user_id", user_id);
    if (agent_id != NULL) cJSON_AddStringToObject(root, "agent_id", agent_id);

    char *body = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (body == NULL) {
        set_error(error_out, error_out_len, "mem0_add_memory: failed to serialize request body");
        return false;
    }

    /* Single-threaded CLI, one call at a time -- init/cleanup per call keeps
     * this module free of process-lifetime global state rather than
     * threading a one-time curl_global_init through main.c for a feature
     * most builds and most runs never touch. */
    if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) {
        free(body);
        set_error(error_out, error_out_len, "mem0_add_memory: curl_global_init failed");
        return false;
    }

    CURL *curl = curl_easy_init();
    if (curl == NULL) {
        free(body);
        curl_global_cleanup();
        set_error(error_out, error_out_len, "mem0_add_memory: curl_easy_init failed");
        return false;
    }

    char auth_header[600];
    snprintf(auth_header, sizeof(auth_header), "Authorization: Token %s", api_key);

    struct curl_slist *headers = NULL;
    headers = curl_slist_append(headers, "Content-Type: application/json");
    headers = curl_slist_append(headers, auth_header);

    Mem0ResponseBuffer response = {0};

    curl_easy_setopt(curl, CURLOPT_URL, MEM0_API_URL);
    curl_easy_setopt(curl, CURLOPT_POST, 1L);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body);
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, mem0_write_cb);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 15L);
    curl_easy_setopt(curl, CURLOPT_USERAGENT, "farm-c-mem0-client/1.0");

    CURLcode res = curl_easy_perform(curl);
    bool ok = false;
    if (res != CURLE_OK) {
        set_error(error_out, error_out_len, curl_easy_strerror(res));
    } else {
        long status = 0;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
        if (status >= 200 && status < 300) {
            ok = true;
        } else {
            char msg[MEM0_ERROR_BUFFER_SIZE];
            snprintf(msg, sizeof(msg), "mem0 API returned HTTP %ld: %.400s", status,
                    response.data != NULL ? response.data : "");
            set_error(error_out, error_out_len, msg);
        }
    }

    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
    curl_global_cleanup();
    free(response.data);
    free(body);
    return ok;
}

#else /* !FARM_WITH_MEM0 */

bool mem0_client_configured(void) {
    return false;
}

bool mem0_add_memory(const char *text, const char *user_id, const char *agent_id,
                     char *error_out, size_t error_out_len) {
    (void)text;
    (void)user_id;
    (void)agent_id;
    if (error_out != NULL && error_out_len > 0) {
        snprintf(error_out, error_out_len,
                "farm-c was built without mem0 support -- rebuild with `make WITH_MEM0=1`");
    }
    return false;
}

#endif /* FARM_WITH_MEM0 */
