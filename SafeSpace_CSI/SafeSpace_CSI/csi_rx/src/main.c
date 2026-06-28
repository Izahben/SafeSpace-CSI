#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"
#include <string.h>
#include <math.h>

// ─── CONFIG ───────────────────────────────────────────────────
#define WIFI_SSID       "CSI_NET"
#define WIFI_PASS       "csi12345"
#define UDP_PORT        3333
#define CSI_BUFFER_LEN  128
// ──────────────────────────────────────────────────────────────

static const char *TAG = "CSI_RX";
static QueueHandle_t csi_queue;

typedef struct {
    int8_t  buf[CSI_BUFFER_LEN];
    uint8_t len;
    int8_t  rssi;
    uint8_t channel;
} csi_packet_t;

// ── CSI CALLBACK ─────────────────────────────────────────────
static void csi_callback(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf) return;

    csi_packet_t pkt;
    pkt.rssi    = info->rx_ctrl.rssi;
    pkt.channel = info->rx_ctrl.channel;
    pkt.len     = (info->len < CSI_BUFFER_LEN) ? info->len : CSI_BUFFER_LEN;
    memcpy(pkt.buf, info->buf, pkt.len);

    xQueueOverwrite(csi_queue, &pkt);
}

// ── COMPUTE MEAN AMPLITUDE FROM CSI SUBCARRIERS ──────────────
static float compute_amplitude(const int8_t *buf, uint8_t len) {
    float sum = 0.0f;
    uint8_t count = 0;
    // CSI buffer is interleaved [imag, real] pairs
    for (int i = 0; i + 1 < len; i += 2) {
        float real = (float)buf[i + 1];
        float imag = (float)buf[i];
        sum += sqrtf(real * real + imag * imag);
        count++;
    }
    return (count > 0) ? (sum / count) : 0.0f;
}

// ── SERIAL OUTPUT TASK ───────────────────────────────────────
static void serial_task(void *pvParam) {
    csi_packet_t pkt;
    while (1) {
        if (xQueueReceive(csi_queue, &pkt, portMAX_DELAY)) {
            float amp = compute_amplitude(pkt.buf, pkt.len);
            // CSV format: AMPLITUDE,RSSI,CHANNEL
            // Python reads this line over serial
            printf("CSI,%.4f,%d,%d\n", amp, pkt.rssi, pkt.channel);
        }
    }
}

// ── WIFI AP + CSI ENABLE ─────────────────────────────────────
static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_AP_START) {
        ESP_LOGI(TAG, "Soft-AP started — SSID: %s", WIFI_SSID);

        // Enable CSI
        wifi_csi_config_t csi_cfg = {
            .lltf_en           = true,
            .htltf_en          = true,
            .stbc_htltf2_en    = true,
            .ltf_merge_en      = true,
            .channel_filter_en = false,
            .manu_scale        = false,
            .shift             = false,
        };
        esp_wifi_set_csi_config(&csi_cfg);
        esp_wifi_set_csi_rx_cb(csi_callback, NULL);
        esp_wifi_set_csi(true);
        ESP_LOGI(TAG, "CSI enabled");
    }
}

void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    csi_queue = xQueueCreate(1, sizeof(csi_packet_t));

    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_ap();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                               wifi_event_handler, NULL);

    wifi_config_t ap_cfg = {};
    strncpy((char *)ap_cfg.ap.ssid,     WIFI_SSID, 32);
    strncpy((char *)ap_cfg.ap.password, WIFI_PASS,  64);
    ap_cfg.ap.ssid_len       = strlen(WIFI_SSID);
    ap_cfg.ap.channel        = 1;
    ap_cfg.ap.authmode       = WIFI_AUTH_WPA2_PSK;
    ap_cfg.ap.max_connection = 4;

    esp_wifi_set_mode(WIFI_MODE_AP);
    esp_wifi_set_config(WIFI_IF_AP, &ap_cfg);
    esp_wifi_start();

    ESP_LOGI(TAG, "RX firmware started");
    xTaskCreate(serial_task, "serial_task", 4096, NULL, 5, NULL);
}
