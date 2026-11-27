/* C ABI of the Rust `termcore` crate. Keep in sync with core/src/lib.rs. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct Core Core;
typedef void (*TermCallback)(void *user, int kind);

enum { TERM_EV_WAKEUP = 0, TERM_EV_TITLE = 1, TERM_EV_BELL = 2, TERM_EV_CLIPBOARD = 3, TERM_EV_EXIT = 4 };

enum {
    CF_BOLD = 1, CF_ITALIC = 2, CF_UL_SINGLE = 4, CF_UL_DOUBLE = 8, CF_UL_CURLY = 16,
    CF_UL_DOTTED = 32, CF_UL_DASHED = 64, CF_STRIKE = 128, CF_WIDE = 256, CF_SPACER = 512,
    CF_SELECTED = 1024, CF_HAS_EXTRA = 2048, CF_LINK = 4096
};
enum { MODE_MOUSE = 1, MODE_ALT_SCREEN = 2, MODE_APP_CURSOR = 4, MODE_BRACKETED_PASTE = 8, MODE_FOCUS_REPORT = 16 };

enum { TK_CHAR = 0, TK_ENTER, TK_TAB, TK_BACKSPACE, TK_ESCAPE, TK_UP, TK_DOWN, TK_LEFT, TK_RIGHT,
       TK_HOME, TK_END, TK_PGUP, TK_PGDN, TK_INSERT, TK_DELETE, TK_F1 = 20 };
enum { TM_SHIFT = 1, TM_ALT = 2, TM_CTRL = 4, TM_SUPER = 8 };
enum { TEV_PRESS = 1, TEV_REPEAT = 2, TEV_RELEASE = 3 };
enum { TACT_PRESS = 0, TACT_RELEASE = 1, TACT_MOTION = 2 };

typedef struct { uint32_t ch, fg, bg, ul, flags; } CCell;
typedef struct {
    int32_t cols, rows, cursor_col, cursor_row, cursor_shape, cursor_blink, display_offset, history_size;
    uint32_t mode;
    int32_t has_selection;
    uint32_t fg, bg, cursor_color, cursor_text, sel_bg;
} CInfo;

Core *term_new(const char *program, const char *const *argv, const char *cwd, int cols, int rows,
               int scrollback, TermCallback cb, void *user);
Core *term_new_log(int cols, int rows, int scrollback, TermCallback cb, void *user);
void term_free(Core *);
void term_str_free(char *);
void term_resize(Core *, int cols, int rows, int cell_w, int cell_h);
void term_set_theme(Core *, uint32_t fg, uint32_t bg, uint32_t cursor, uint32_t cursor_text, uint32_t sel_bg,
                    const uint32_t *ansi16);
int term_snapshot(Core *, CCell *cells, size_t cap, CInfo *info);
size_t term_cell_extra(Core *, int col, int row, uint32_t *out, size_t cap);
void term_write(Core *, const uint8_t *data, size_t len);
void term_feed(Core *, const uint8_t *data, size_t len);
void term_set_pty_mute(Core *, int muted, const char *marker);
void term_paste(Core *, const char *utf8);
void term_focus(Core *, int focused);
int term_key(Core *, int key, uint32_t base, const char *text, uint32_t mods, int event);
int term_mouse(Core *, int button, int action, int col, int row, uint32_t mods);
void term_scroll(Core *, int lines, int col, int row, uint32_t mods);
void term_scroll_local(Core *, int mode, int lines);
void term_sel_start(Core *, int kind, int col, int row, int right_half);
void term_sel_update(Core *, int col, int row, int right_half);
void term_sel_clear(Core *);
void term_sel_all(Core *);
char *term_sel_text(Core *);
char *term_link_at(Core *, int col, int row, int32_t *range4);
char *term_title(Core *);
char *term_take_clipboard(Core *);
int term_alive(Core *);
int term_exit_code(Core *);

#ifdef __cplusplus
}
#endif
