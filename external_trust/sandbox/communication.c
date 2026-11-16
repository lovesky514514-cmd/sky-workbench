/* 文档规范化。无导入，无文件、工具或网络能力。 */
static unsigned char input[65536];

unsigned char *buffer(void) { return input; }

int process(int length) {
    if (length < 1 || length > (int)sizeof(input)) return -1;
    int out = 0;
    for (int i = 0; i < length; ++i) {
        unsigned char value = input[i];
        if (value == '\r') {
            if (i + 1 < length && input[i + 1] == '\n') ++i;
            value = '\n';
        }
        if ((value < 32 && value != '\n' && value != '\t') || value == 127) return -1;
        input[out++] = value;
    }
    while (out > 0 && (input[out - 1] == ' ' || input[out - 1] == '\n' || input[out - 1] == '\t')) --out;
    return out;
}

int negotiate(int kind, int state, int opposite_direction, int reply_matches) {
    if (kind == 1 && state == 0) return 1;
    if ((kind == 2 || kind == 3) && state == 1 && opposite_direction && reply_matches) return kind;
    return -1;
}
