/* 仅生成风险位图。授权与拒绝由网关决定。 */
static unsigned char input[65536];
unsigned char *buffer(void) { return input; }

struct rule { const char *pattern; int flag; };
static const struct rule rules[] = {
    {"ignore previous", 1}, {"ignore all instructions", 1}, {"system prompt", 1},
    {"<|system|>", 1}, {"忽略之前", 1}, {"忽略以上", 1}, {"覆盖系统指令", 1},
    {"tool_call", 2}, {"shell:", 2}, {"powershell", 2}, {"cmd.exe", 2},
    {"curl ", 2}, {"run_comsol", 2}, {"execute command", 2}, {"调用工具", 2},
    {"write memory", 4}, {"overwrite memory", 4}, {"permanent instruction", 4},
    {"remember this instruction", 4}, {"写入长期记忆", 4}, {"覆盖记忆", 4}, {"永久指令", 4},
    {"javascript:", 8}, {"<script", 8}, {"<iframe", 8}, {"data:text/html", 8}, {"file://", 8},
    {"169.254.169.254", 8}, {"localhost:", 8}, {"127.0.0.1:", 8}, {"gopher:", 8},
    {"private key-----", 16}, {"authorization: bearer", 16}, {"api_key=", 16},
    {"password=", 16}, {"sk-", 16}, {"\\users\\", 16}, {"/home/", 16},
    {"forward to every", 32}, {"forward to all", 32}, {"自动转发", 32}, {"广播给所有", 32},
};

static unsigned char lower(unsigned char value) {
    return value >= 'A' && value <= 'Z' ? value + ('a' - 'A') : value;
}

int process(int length) {
    if (length < 1 || length > (int)sizeof(input)) return -1;
    int flags = 0;
    for (unsigned int r = 0; r < sizeof(rules) / sizeof(rules[0]); ++r) {
        if (flags & rules[r].flag) continue;
        for (int i = 0; i < length; ++i) {
            int j = 0;
            while (i + j < length && rules[r].pattern[j] && lower(input[i + j]) == (unsigned char)rules[r].pattern[j]) ++j;
            if (!rules[r].pattern[j]) { flags |= rules[r].flag; break; }
        }
    }
    return flags;
}
