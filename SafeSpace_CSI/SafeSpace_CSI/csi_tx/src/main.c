#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"
#include <string.h>

// ─── CONFIG ───────────────────────────────────────────────────
#define WIFI_SSID       "CSI_NET"
#define WIFI_PASS       "csi12345"
#define UDP_TARGET_IP   "192.168.4.2"   // RX soft-AP address (change if needed)
#define UDP_PORT        3333
#define PING_INTERVAL_MS 50             // 20 packets/sec
// ──────────────────────────────────────────────────────────────

static const char *TAG = "CSI_TX";
static int udp_sock = -1;
static struct sockaddr_in dest_addr;

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_CONNECTED) {
        ESP_LOGI(TAG, "Connected to AP — starting UDP ping");
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));

        // Create UDP socket
        udp_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
        memset(&dest_addr, 0, sizeof(dest_addr));
        dest_addr.sin_family      = AF_INET;
        dest_addr.sin_port        = htons(UDP_PORT);
        inet_aton(UDP_TARGET_IP, &dest_addr.sin_addr);
    }
}

static void ping_task(void *pvParam) {
    uint32_t seq = 0;
    char buf[32];
    while (1) {
        if (udp_sock >= 0) {
            int len = snprintf(buf, sizeof(buf), "PING %lu", (unsigned long)seq++);
            sendto(udp_sock, buf, len, 0,
                   (struct sockaddr *)&dest_addr, sizeof(dest_addr));
        }
        vTaskDelay(pdMS_TO_TICKS(PING_INTERVAL_MS));
    }
}

void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                               wifi_event_handler, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                               wifi_event_handler, NULL);

    wifi_config_t wifi_cfg = {};
    strncpy((char *)wifi_cfg.sta.ssid,     WIFI_SSID, 32);
    strncpy((char *)wifi_cfg.sta.password, WIFI_PASS,  64);

    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
    esp_wifi_start();

    ESP_LOGI(TAG, "TX firmware started — connecting to %s", WIFI_SSID);
    xTaskCreate(ping_task, "ping_task", 4096, NULL, 5, NULL);
}
