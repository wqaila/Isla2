/* 第一版长度上限，必须与 gateway/app.py 的校验保持一致。
 * 由 tests/test_firmware_contract.py 校验，改动时两边同时改。 */

// 用户单条消息最大 UTF-8 字节数（网关同样是 512）
#define APP_MAX_MESSAGE_BYTES     512

// 请求体最大字节数（网关中间件 2048）
#define APP_MAX_REQUEST_BYTES     2048

// 响应体最大字节数（网关断言 8192）
#define APP_MAX_RESPONSE_BYTES    8192

// 串口一行的最大字节数，含换行符余量
#define APP_MAX_LINE_BYTES        (APP_MAX_MESSAGE_BYTES + 8)

// 答案在屏幕上的最大保存长度，超出部分由滚动分段处理
#define APP_MAX_ANSWER_CHARS      2048

// 网关地址与令牌上限，避免缓冲区无界
#define APP_MAX_BASE_URL_CHARS    192
#define APP_MAX_TOKEN_CHARS       128

// 单次 HTTP 请求总超时（毫秒）。网关自身超时是 45 秒。
#define APP_HTTP_TIMEOUT_MS       60000

// 会话标识显示/保存长度
#define APP_SESSION_ID_CHARS      96
