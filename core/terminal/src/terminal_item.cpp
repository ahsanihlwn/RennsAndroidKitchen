#include "terminal_item.h"

#include <QClipboard>
#include <QCursor>
#include <QQuickWindow>
#include <QDesktopServices>
#include <QGuiApplication>
#include <QInputMethod>
#include <QInputMethodEvent>
#include <QKeyEvent>
#include <QPainter>
#include <QPainterPath>
#include <QStyleHints>
#include <QUrl>
#include <QtMath>
#include <cmath>

namespace {

inline QColor rgb(uint32_t v) { return QColor((v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff); }
inline uint32_t pack(const QColor &c) { return (uint32_t(c.red()) << 16) | (uint32_t(c.green()) << 8) | uint32_t(c.blue()); }

const char *kTango[16] = {"#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf",
                          "#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"};

// Box drawing U+2500..U+254F as "lrud" weights (0 none, 1 light, 2 heavy). Dashed variants are drawn solid.
const char *kBox[0x50] = {
    "1100", "2200", "0011", "0022", "1100", "2200", "0011", "0022", "1100", "2200", "0011", "0022", "0101", "0201", "0102", "0202",
    "1001", "2001", "1002", "2002", "0110", "0210", "0120", "0220", "1010", "2010", "1020", "2020", "0111", "0211", "0121", "0112",
    "0122", "0221", "0212", "0222", "1011", "2011", "1021", "1012", "1022", "2021", "2012", "2022", "1101", "2101", "1201", "2201",
    "1102", "2102", "1202", "2202", "1110", "2110", "1210", "2210", "1120", "2120", "1220", "2220", "1111", "2111", "1211", "2211",
    "1121", "1112", "1122", "2121", "1221", "2112", "1212", "2221", "2212", "2122", "1222", "2222", "1100", "2200", "0011", "0022"};
// U+2574..U+257F
const char *kBoxTail[12] = {"1000", "0010", "0100", "0001", "2000", "0020", "0200", "0002", "1200", "0012", "2100", "0021"};

int mapQtKey(int k) {
    switch (k) {
    case Qt::Key_Return: case Qt::Key_Enter: return TK_ENTER;
    case Qt::Key_Tab: case Qt::Key_Backtab: return TK_TAB;
    case Qt::Key_Backspace: return TK_BACKSPACE;
    case Qt::Key_Escape: return TK_ESCAPE;
    case Qt::Key_Up: return TK_UP;
    case Qt::Key_Down: return TK_DOWN;
    case Qt::Key_Left: return TK_LEFT;
    case Qt::Key_Right: return TK_RIGHT;
    case Qt::Key_Home: return TK_HOME;
    case Qt::Key_End: return TK_END;
    case Qt::Key_PageUp: return TK_PGUP;
    case Qt::Key_PageDown: return TK_PGDN;
    case Qt::Key_Insert: return TK_INSERT;
    case Qt::Key_Delete: return TK_DELETE;
    default:
        if (k >= Qt::Key_F1 && k <= Qt::Key_F24) return TK_F1 + (k - Qt::Key_F1);
        return TK_CHAR;
    }
}

bool isModifierOnly(int k) {
    switch (k) {
    case Qt::Key_Shift: case Qt::Key_Control: case Qt::Key_Alt: case Qt::Key_AltGr: case Qt::Key_Meta:
    case Qt::Key_Super_L: case Qt::Key_Super_R: case Qt::Key_Hyper_L: case Qt::Key_Hyper_R:
    case Qt::Key_CapsLock: case Qt::Key_NumLock: case Qt::Key_ScrollLock: case Qt::Key_Mode_switch:
    case Qt::Key_unknown: return true;
    default: return k >= Qt::Key_Dead_Grave && k <= Qt::Key_Dead_Longsolidusoverlay;
    }
}

/// Qt reports Shift+1 as Key_Exclam etc. Map back to the unshifted US-layout key for the kitty protocol.
uint32_t unshift(uint32_t c) {
    static const char *from = "!@#$%^&*()_+{}|:\"<>?~";
    static const char *to = "1234567890-=[]\\;',./`";
    for (int i = 0; from[i]; ++i)
        if (char(c) == from[i]) return uint32_t(to[i]);
    return c;
}

} // namespace

// =====================================================================================

TerminalItem::TerminalItem(QQuickItem *parent) : QQuickPaintedItem(parent) {
    setFlag(ItemAcceptsInputMethod, true);
    setAcceptedMouseButtons(Qt::AllButtons);
    setAcceptHoverEvents(true);
    setActiveFocusOnTab(true);
    setCursor(Qt::IBeamCursor);
    setOpaquePainting(true);
    for (auto c : kTango) palette_.append(QColor(c));
    blinkTimer_.setInterval(530);
    connect(&blinkTimer_, &QTimer::timeout, this, [this] { ++blinkFires_; cursorOn_ = !cursorOn_; update(); });
    scrollTimer_.setInterval(40);
    connect(&scrollTimer_, &QTimer::timeout, this, &TerminalItem::autoScrollTick);
    updateMetrics();
}

TerminalItem::~TerminalItem() { stop(); }

// ---- lifecycle ----------------------------------------------------------------------

void TerminalItem::componentComplete() {
    QQuickPaintedItem::componentComplete();
    fit();
    maybeStart();
}

void TerminalItem::maybeStart() {
    if (!term_ && autoStart_ && isComponentComplete() && cols_ >= 2 && rows_ >= 1) start();
}

void TerminalItem::start() {
    stop();
    if (cols_ < 2 || rows_ < 1) return;
    QByteArray prog = program_.toUtf8(), cwd = cwd_.toUtf8();
    std::vector<QByteArray> args;
    for (const auto &a : arguments_) args.push_back(a.toUtf8());
    std::vector<const char *> argv;
    for (const auto &a : args) argv.push_back(a.constData());
    argv.push_back(nullptr);
    term_ = term_new(program_.isEmpty() ? nullptr : prog.constData(), argv.data(),
                     cwd_.isEmpty() ? nullptr : cwd.constData(), cols_, rows_, scrollback_, &TerminalItem::onCoreEvent, this);
    if (!term_) return;
    applyTheme();
    term_resize(term_, cols_, rows_, int(cellW_), int(cellH_));
    if (hasActiveFocus()) term_focus(term_, 1);
    emit runningChanged();
    refresh();
}

void TerminalItem::stop() {
    if (!term_) return;
    Core *t = term_;
    term_ = nullptr;
    term_free(t); // blocks until no callback is in flight
    wakePending_ = false;
    cells_.clear();
    info_ = CInfo{};
    emit runningChanged();
    update();
}

void TerminalItem::onCoreEvent(void *user, int kind) {
    auto *self = static_cast<TerminalItem *>(user);
    if (kind == TERM_EV_WAKEUP) {
        if (!self->wakePending_.exchange(true))
            QMetaObject::invokeMethod(self, [self] { self->scheduleRefresh(); }, Qt::QueuedConnection);
    } else {
        QMetaObject::invokeMethod(self, [self, kind] { self->handleEvent(kind); }, Qt::QueuedConnection);
    }
}

// Coalesce output bursts into at most ~120 snapshots/s (min 8 ms apart) so floods never starve the UI thread.
void TerminalItem::scheduleRefresh() {
    const qint64 since = lastRefresh_.isValid() ? lastRefresh_.elapsed() : 1000;
    const int delay = since >= 8 ? 0 : int(8 - since);
    QTimer::singleShot(delay, Qt::PreciseTimer, this, [this] {
        wakePending_ = false; // clear first: output arriving during the snapshot re-arms us
        lastRefresh_.restart();
        refresh();
    });
}

void TerminalItem::handleEvent(int kind) {
    if (!term_) return;
    switch (kind) {
    case TERM_EV_TITLE: {
        char *t = term_title(term_);
        title_ = QString::fromUtf8(t);
        term_str_free(t);
        emit titleChanged();
        break;
    }
    case TERM_EV_BELL: emit bell(); break;
    case TERM_EV_CLIPBOARD: {
        if (char *t = term_take_clipboard(term_)) { // OSC 52 copy
            QGuiApplication::clipboard()->setText(QString::fromUtf8(t));
            term_str_free(t);
        }
        break;
    }
    case TERM_EV_EXIT:
        refresh();
        emit runningChanged();
        emit exited(term_exit_code(term_));
        break;
    }
}

void TerminalItem::refresh() {
    if (!term_) return;
    QElapsedTimer snapT; snapT.start();
    ++refreshCount_;
    CInfo info{};
    int rc = term_snapshot(term_, cells_.data(), cells_.size(), &info);
    if (rc != 0) {
        cells_.resize(size_t(info.cols) * size_t(info.rows));
        rc = term_snapshot(term_, cells_.data(), cells_.size(), &info);
        if (rc != 0) return;
    }
    const bool scrollDiff = info.history_size != info_.history_size || info.display_offset != info_.display_offset ||
                            (info.mode & MODE_MOUSE) != (info_.mode & MODE_MOUSE);
    const bool selDiff = info.has_selection != info_.has_selection;
    const bool cursorMoved = info.cursor_col != info_.cursor_col || info.cursor_row != info_.cursor_row;
    snapNs_ += snapT.nsecsElapsed();
    info_ = info;
    if (scrollDiff) emit scrollChanged();
    if (selDiff) emit selectionChanged();
    if (cursorMoved && hasActiveFocus()) QGuiApplication::inputMethod()->update(Qt::ImCursorRectangle);
    if (hoverLink_.valid) clearHoverLink(); // content may have moved under the pointer
    update();
}

// ---- metrics / theme ----------------------------------------------------------------

void TerminalItem::updateMetrics() {
    for (int i = 0; i < 4; ++i) {
        QFont f(family_);
        f.setPixelSize(pixelSize_);
        f.setStyleHint(QFont::Monospace);
        f.setFixedPitch(true);
        f.setKerning(false);
        f.setBold(i & 1);
        f.setItalic(i & 2);
        fonts_[i] = f;
    }
    QFontMetricsF fm(fonts_[0]);
    const qreal adv = fm.horizontalAdvance(QLatin1Char('M'));
    cellW_ = qMax<qreal>(1, std::round(adv));
    for (auto &f : fonts_) f.setLetterSpacing(QFont::AbsoluteSpacing, cellW_ - adv); // keep text runs on the grid
    cellH_ = qCeil(fm.height());
    ascent_ = fm.ascent() + (cellH_ - fm.height()) / 2;
}

void TerminalItem::fit() {
    if (width() <= 0 || height() <= 0) return;
    const int c = qMax(2, int((width() - 2 * padding_) / cellW_));
    const int r = qMax(1, int((height() - 2 * padding_) / cellH_));
    const bool changed = c != cols_ || r != rows_;
    cols_ = c;
    rows_ = r;
    if (changed) emit sizeChanged();
    if (term_) {
        term_resize(term_, c, r, int(cellW_), int(cellH_));
        refresh();
    }
}

void TerminalItem::geometryChange(const QRectF &n, const QRectF &o) {
    QQuickPaintedItem::geometryChange(n, o);
    if (n.size() != o.size()) {
        fit();
        maybeStart();
    }
}

void TerminalItem::applyTheme() {
    if (!term_) return;
    uint32_t ansi[16];
    for (int i = 0; i < 16; ++i) ansi[i] = pack(palette_.value(i, QColor(kTango[i])));
    QColor sel = selColor_;
    if (sel.alpha() < 255) { // blend translucent selection over the background (xterm.js behaviour)
        const qreal a = sel.alphaF();
        sel = QColor(qRound(sel.red() * a + bg_.red() * (1 - a)), qRound(sel.green() * a + bg_.green() * (1 - a)),
                     qRound(sel.blue() * a + bg_.blue() * (1 - a)));
    }
    term_set_theme(term_, pack(fg_), pack(bg_), pack(cursorColor_), pack(bg_), pack(sel), ansi);
    refresh();
}

#define SETTER(Type, name, member, sig) \
    void TerminalItem::name(const Type &v) { if (member == v) return; member = v; sig; }
void TerminalItem::setForeground(const QColor &v) { if (fg_ == v) return; fg_ = v; applyTheme(); emit themeChanged(); }
void TerminalItem::setBackground(const QColor &v) { if (bg_ == v) return; bg_ = v; applyTheme(); emit themeChanged(); update(); }
void TerminalItem::setCursorColor(const QColor &v) { if (cursorColor_ == v) return; cursorColor_ = v; applyTheme(); emit themeChanged(); }
void TerminalItem::setSelectionColor(const QColor &v) { if (selColor_ == v) return; selColor_ = v; applyTheme(); emit themeChanged(); }
void TerminalItem::setFontFamily(const QString &v) {
    if (family_ == v) return;
    family_ = v; updateMetrics(); fit(); maybeStart(); emit fontChanged(); update();
}
void TerminalItem::setFontPixelSize(int v) {
    if (pixelSize_ == v || v < 4) return;
    pixelSize_ = v; updateMetrics(); fit(); maybeStart(); emit fontChanged(); update();
}
void TerminalItem::setPadding(int v) {
    if (padding_ == v || v < 0) return;
    padding_ = v; fit(); maybeStart(); emit fontChanged(); update();
}
QVariantList TerminalItem::palette() const {
    QVariantList l;
    for (const auto &c : palette_) l << c;
    return l;
}
void TerminalItem::setPalette(const QVariantList &p) {
    QList<QColor> n;
    for (const auto &v : p) n << v.value<QColor>();
    while (n.size() < 16) n << QColor(kTango[n.size()]);
    palette_ = n;
    applyTheme();
    emit themeChanged();
}
QFont TerminalItem::fontFor(uint32_t flags) const { return fonts_[(flags & CF_BOLD ? 1 : 0) | (flags & CF_ITALIC ? 2 : 0)]; }

// ---- painting -----------------------------------------------------------------------

static bool drawBoxOrBlock(QPainter *p, uint32_t ch, qreal x, qreal y, qreal w, qreal h, const QColor &col) {
    // Block elements
    if (ch >= 0x2580 && ch <= 0x259F) {
        auto frac = [&](qreal fx0, qreal fy0, qreal fx1, qreal fy1, QColor c = QColor()) {
            const qreal X0 = x + std::round(w * fx0), X1 = x + std::round(w * fx1);
            const qreal Y0 = y + std::round(h * fy0), Y1 = y + std::round(h * fy1);
            p->fillRect(QRectF(X0, Y0, X1 - X0, Y1 - Y0), c.isValid() ? c : col);
        };
        if (ch == 0x2580) frac(0, 0, 1, 0.5);
        else if (ch >= 0x2581 && ch <= 0x2588) frac(0, 1 - (ch - 0x2580) / 8.0, 1, 1);
        else if (ch >= 0x2589 && ch <= 0x258F) frac(0, 0, (0x2590 - ch) / 8.0, 1);
        else if (ch == 0x2590) frac(0.5, 0, 1, 1);
        else if (ch >= 0x2591 && ch <= 0x2593) { QColor c = col; c.setAlphaF((ch - 0x2590) * 0.25); frac(0, 0, 1, 1, c); }
        else if (ch == 0x2594) frac(0, 0, 1, 1 / 8.0);
        else if (ch == 0x2595) frac(7 / 8.0, 0, 1, 1);
        else {
            // quadrants: bit0 UL, bit1 UR, bit2 LL, bit3 LR
            static const int q[10] = {4, 8, 1, 1 | 4 | 8, 1 | 8, 1 | 2 | 4, 1 | 2 | 8, 2, 2 | 4, 2 | 4 | 8};
            const int m = q[ch - 0x2596];
            if (m & 1) frac(0, 0, 0.5, 0.5);
            if (m & 2) frac(0.5, 0, 1, 0.5);
            if (m & 4) frac(0, 0.5, 0.5, 1);
            if (m & 8) frac(0.5, 0.5, 1, 1);
        }
        return true;
    }
    // Rounded corners
    if (ch >= 0x256D && ch <= 0x2570) {
        const qreal t = qMax<qreal>(1, std::round(h / 14.0));
        const qreal cx = x + std::floor(w / 2) + t / 2, cy = y + std::floor(h / 2) + t / 2;
        QPainterPath path;
        switch (ch) {
        case 0x256D: path.moveTo(cx, y + h); path.quadTo(cx, cy, x + w, cy); break; // right+down
        case 0x256E: path.moveTo(cx, y + h); path.quadTo(cx, cy, x, cy); break;     // left+down
        case 0x256F: path.moveTo(cx, y); path.quadTo(cx, cy, x, cy); break;         // left+up
        default: path.moveTo(cx, y); path.quadTo(cx, cy, x + w, cy); break;         // right+up
        }
        p->save();
        p->setRenderHint(QPainter::Antialiasing, true);
        p->setPen(QPen(col, t, Qt::SolidLine, Qt::FlatCap));
        p->setBrush(Qt::NoBrush);
        p->drawPath(path);
        p->restore();
        return true;
    }
    const char *spec = nullptr;
    if (ch >= 0x2500 && ch <= 0x254F) spec = kBox[ch - 0x2500];
    else if (ch >= 0x2574 && ch <= 0x257F) spec = kBoxTail[ch - 0x2574];
    if (!spec) return false; // double lines etc: let the font draw them
    const qreal light = qMax<qreal>(1, std::round(h / 14.0)), heavy = light * 2;
    const qreal cx = x + std::floor(w / 2), cy = y + std::floor(h / 2);
    const int l = spec[0] - '0', r = spec[1] - '0', u = spec[2] - '0', d = spec[3] - '0';
    auto th = [&](int wgt) { return wgt == 2 ? heavy : light; };
    const qreal vt = qMax(l ? th(l) : 0, r ? th(r) : 0), ht = qMax(u ? th(u) : 0, d ? th(d) : 0); // junction size
    if (l) p->fillRect(QRectF(x, cy - th(l) / 2, cx - x + ht / 2 + 0.0, th(l)), col);
    if (r) p->fillRect(QRectF(cx - ht / 2 + ht / 2, cy - th(r) / 2, x + w - cx - ht / 2 + ht / 2, th(r)), col);
    if (u) p->fillRect(QRectF(cx - th(u) / 2, y, th(u), cy - y + vt / 2), col);
    if (d) p->fillRect(QRectF(cx - th(d) / 2, cy - vt / 2 + vt / 2, th(d), y + h - cy - vt / 2 + vt / 2), col);
    return true;
}

void TerminalItem::paint(QPainter *p) {
    QElapsedTimer paintT; paintT.start();
    struct Done { TerminalItem *t; QElapsedTimer &e; ~Done() { ++t->paintCount_; t->paintNs_ += e.nsecsElapsed(); } } done{this, paintT};
    const bool have = term_ && !cells_.empty() && size_t(info_.cols) * size_t(info_.rows) <= cells_.size() && info_.cols > 0;
    p->fillRect(boundingRect(), have ? rgb(info_.bg) : bg_);
    if (!have) return;

    const int cols = info_.cols, rows = info_.rows;
    const qreal x0 = padding_, y0 = padding_;
    auto at = [&](int c, int r) -> const CCell & { return cells_[size_t(r) * cols + c]; };
    p->setRenderHint(QPainter::TextAntialiasing, true);

    // 1) backgrounds (merged runs)
    for (int r = 0; r < rows; ++r) {
        int c = 0;
        while (c < cols) {
            const uint32_t b = at(c, r).bg;
            const int s = c;
            while (c < cols && at(c, r).bg == b) ++c;
            if (b != info_.bg) p->fillRect(QRectF(x0 + s * cellW_, y0 + r * cellH_, (c - s) * cellW_, cellH_), rgb(b));
        }
    }

    // 2) glyphs
    auto glyphText = [&](const CCell &cc, int c, int r) {
        QString s = QString::fromUcs4(reinterpret_cast<const char32_t *>(&cc.ch), 1);
        if (cc.flags & CF_HAS_EXTRA) {
            uint32_t ex[8];
            const size_t n = term_cell_extra(term_, c, r, ex, 8);
            for (size_t i = 0; i < n; ++i) s += QString::fromUcs4(reinterpret_cast<const char32_t *>(&ex[i]), 1);
        }
        return s;
    };
    for (int r = 0; r < rows; ++r) {
        const qreal top = y0 + r * cellH_, base = top + ascent_;
        int c = 0;
        while (c < cols) {
            const CCell &cc = at(c, r);
            if ((cc.flags & CF_SPACER) || cc.ch == ' ') { ++c; continue; }
            const bool ascii = cc.ch > 32 && cc.ch < 127 && !(cc.flags & (CF_HAS_EXTRA | CF_WIDE));
            if (ascii) {
                const uint32_t style = cc.flags & (CF_BOLD | CF_ITALIC), fgc = cc.fg;
                const int s = c;
                QString run;
                while (c < cols) {
                    const CCell &d = at(c, r);
                    if ((d.flags & (CF_SPACER | CF_HAS_EXTRA | CF_WIDE)) || d.ch < 32 || d.ch >= 127 || d.fg != fgc ||
                        (d.flags & (CF_BOLD | CF_ITALIC)) != style)
                        break;
                    run.append(QChar(d.ch));
                    ++c;
                }
                p->setFont(fontFor(style));
                p->setPen(rgb(fgc));
                p->drawText(QPointF(x0 + s * cellW_, base), run);
            } else {
                const qreal x = x0 + c * cellW_, w = ((cc.flags & CF_WIDE) ? 2 : 1) * cellW_;
                if (!drawBoxOrBlock(p, cc.ch, x, top, w, cellH_, rgb(cc.fg))) {
                    p->setFont(fontFor(cc.flags));
                    p->setPen(rgb(cc.fg));
                    p->drawText(QPointF(x, base), glyphText(cc, c, r));
                }
                ++c;
            }
        }
    }

    // 3) decorations: underline styles + strikethrough
    const uint32_t ULMASK = CF_UL_SINGLE | CF_UL_DOUBLE | CF_UL_CURLY | CF_UL_DOTTED | CF_UL_DASHED;
    auto drawUnderline = [&](qreal xa, qreal xb, qreal top, uint32_t style, const QColor &col) {
        const qreal y = qMin(top + ascent_ + 1.5, top + cellH_ - 1.5);
        p->save();
        QPen pen(col, 1);
        pen.setCapStyle(Qt::FlatCap);
        if (style & CF_UL_CURLY) {
            p->setRenderHint(QPainter::Antialiasing, true);
            QPainterPath path;
            const qreal period = qMax<qreal>(4, cellW_ / 1.5), amp = qMax<qreal>(1, cellH_ / 16.0);
            path.moveTo(xa, y);
            for (qreal x = xa; x <= xb; x += 0.5) path.lineTo(x, y - amp * std::sin((x - x0) * 2 * M_PI / period) * 0.8 - amp * 0.2);
            p->setPen(pen);
            p->drawPath(path);
        } else if (style & CF_UL_DOUBLE) {
            p->fillRect(QRectF(xa, y - 1, xb - xa, 1), col);
            p->fillRect(QRectF(xa, y + 1, xb - xa, 1), col);
        } else if (style & (CF_UL_DOTTED | CF_UL_DASHED)) {
            pen.setStyle((style & CF_UL_DOTTED) ? Qt::DotLine : Qt::DashLine);
            p->setPen(pen);
            p->drawLine(QPointF(xa, y + 0.5), QPointF(xb, y + 0.5));
        } else {
            p->fillRect(QRectF(xa, y, xb - xa, 1), col);
        }
        p->restore();
    };
    const qreal xh = QFontMetricsF(fonts_[0]).xHeight();
    for (int r = 0; r < rows; ++r) {
        const qreal top = y0 + r * cellH_;
        int c = 0;
        while (c < cols) {
            const CCell &cc = at(c, r);
            const uint32_t ul = cc.flags & ULMASK;
            if (ul) {
                const int s = c;
                while (c < cols && (at(c, r).flags & ULMASK) == ul && at(c, r).ul == cc.ul) ++c;
                drawUnderline(x0 + s * cellW_, x0 + c * cellW_, top, ul, rgb(cc.ul));
            } else {
                ++c;
            }
        }
        for (c = 0; c < cols;) {
            const CCell &cc = at(c, r);
            if (cc.flags & CF_STRIKE) {
                const int s = c;
                while (c < cols && (at(c, r).flags & CF_STRIKE) && at(c, r).fg == cc.fg) ++c;
                p->fillRect(QRectF(x0 + s * cellW_, std::round(top + ascent_ - xh / 2), (c - s) * cellW_, 1), rgb(cc.fg));
            } else {
                ++c;
            }
        }
    }

    // 4) hovered link underline (like xterm.js linkifier)
    if (hoverLink_.valid) {
        for (int r = hoverLink_.r0; r <= hoverLink_.r1 && r < rows; ++r) {
            const int a = (r == hoverLink_.r0) ? hoverLink_.c0 : 0, b = (r == hoverLink_.r1) ? hoverLink_.c1 : cols - 1;
            drawUnderline(x0 + a * cellW_, x0 + (b + 1) * cellW_, y0 + r * cellH_, CF_UL_SINGLE, rgb(at(a, r).fg));
        }
    }

    // 5) cursor
    if (info_.cursor_shape >= 0 && info_.cursor_row >= 0 && info_.cursor_row < rows) {
        const bool focused = hasActiveFocus() && window() && window()->isActive();
        const bool blink = (info_.cursor_blink || cursorBlink_) && focused;
        const int cc = info_.cursor_col, cr = info_.cursor_row;
        const CCell &under = at(cc, cr);
        const int w = (under.flags & CF_WIDE) ? 2 : 1;
        const QRectF rc(x0 + cc * cellW_, y0 + cr * cellH_, w * cellW_, cellH_);
        const QColor ccol = rgb(info_.cursor_color);
        if (!focused) {
            p->setPen(QPen(ccol, 1));
            p->setBrush(Qt::NoBrush);
            p->drawRect(rc.adjusted(0.5, 0.5, -0.5, -0.5));
        } else if (!blink || cursorOn_) {
            switch (info_.cursor_shape) {
            case 1: p->fillRect(QRectF(rc.x(), rc.bottom() - 2, rc.width(), 2), ccol); break;
            case 2: p->fillRect(QRectF(rc.x(), rc.y(), 2, rc.height()), ccol); break;
            default:
                p->fillRect(rc, ccol);
                if (under.ch != ' ' && !(under.flags & CF_SPACER)) {
                    const QColor tcol = rgb(info_.cursor_text);
                    if (!drawBoxOrBlock(p, under.ch, rc.x(), rc.y(), rc.width(), rc.height(), tcol)) {
                        p->setFont(fontFor(under.flags));
                        p->setPen(tcol);
                        p->drawText(QPointF(rc.x(), rc.y() + ascent_), glyphText(under, cc, cr));
                    }
                }
            }
        }
        // 6) IME preedit
        if (!preedit_.isEmpty()) {
            const qreal pw = QFontMetricsF(fonts_[0]).horizontalAdvance(preedit_);
            p->fillRect(QRectF(rc.x(), rc.y(), pw, cellH_), rgb(info_.bg));
            p->setFont(fonts_[0]);
            p->setPen(rgb(info_.fg));
            p->drawText(QPointF(rc.x(), rc.y() + ascent_), preedit_);
            p->fillRect(QRectF(rc.x(), rc.y() + cellH_ - 2, pw, 1), rgb(info_.fg));
        }
    }
}

// ---- keyboard -----------------------------------------------------------------------

uint32_t TerminalItem::modsOf(Qt::KeyboardModifiers m) {
    uint32_t r = 0;
    if (m & Qt::ShiftModifier) r |= TM_SHIFT;
    if (m & Qt::AltModifier) r |= TM_ALT;
#ifdef Q_OS_MACOS // Qt swaps Ctrl/Cmd on macOS
    if (m & Qt::MetaModifier) r |= TM_CTRL;
    if (m & Qt::ControlModifier) r |= TM_SUPER;
#else
    if (m & Qt::ControlModifier) r |= TM_CTRL;
    if (m & Qt::MetaModifier) r |= TM_SUPER;
#endif
    return r;
}

void TerminalItem::sendKey(QKeyEvent *e, int event) {
    const int qk = e->key();
    if (isModifierOnly(qk)) { e->accept(); return; }
    int key = mapQtKey(qk);
    uint32_t mods = modsOf(e->modifiers());
    if (qk == Qt::Key_Backtab) mods |= TM_SHIFT;
    uint32_t base = 0;
    QString text;
    if (key == TK_CHAR) {
        text = e->text();
        if (!text.isEmpty() && (text[0].unicode() < 0x20 || text[0].unicode() == 0x7f)) text.clear();
        if (qk >= 0x20 && qk <= 0x7e) {
            base = (qk >= 'A' && qk <= 'Z') ? qk + 32 : unshift(uint32_t(qk));
        } else if (!text.isEmpty()) {
            base = text.toUcs4().value(0);
        }
    }
    const QByteArray t = text.toUtf8();
    if (term_key(term_, key, base, t.constData(), mods, event)) {
        e->accept();
        cursorOn_ = true;
        if (blinkTimer_.isActive()) { ++blinkStarts_; blinkTimer_.start(); }
        update();
    } else {
        e->ignore();
    }
}

void TerminalItem::keyPressEvent(QKeyEvent *e) {
    if (!term_) { e->ignore(); return; }
    const auto m = e->modifiers();
    const int k = e->key();
#ifdef Q_OS_MACOS
    const bool copyKey = m == Qt::ControlModifier && k == Qt::Key_C, pasteKey = m == Qt::ControlModifier && k == Qt::Key_V;
#else
    const bool copyKey = (m == (Qt::ControlModifier | Qt::ShiftModifier) && k == Qt::Key_C) || (m == Qt::ControlModifier && k == Qt::Key_Insert);
    const bool pasteKey = (m == (Qt::ControlModifier | Qt::ShiftModifier) && k == Qt::Key_V) || (m == Qt::ShiftModifier && k == Qt::Key_Insert);
#endif
    if (copyKey && hasSelection()) { copy(); e->accept(); return; }
    if (copyKey) { e->accept(); return; }
    if (pasteKey) { paste(); e->accept(); return; }
    if (m == Qt::ShiftModifier && (k == Qt::Key_PageUp || k == Qt::Key_PageDown || k == Qt::Key_Home || k == Qt::Key_End)) {
        term_scroll_local(term_, k == Qt::Key_PageUp ? 1 : k == Qt::Key_PageDown ? 2 : k == Qt::Key_Home ? 3 : 4, 0);
        e->accept();
        return;
    }
    sendKey(e, e->isAutoRepeat() ? TEV_REPEAT : TEV_PRESS);
}

void TerminalItem::keyReleaseEvent(QKeyEvent *e) {
    if (!term_ || e->isAutoRepeat()) { e->ignore(); return; }
    sendKey(e, TEV_RELEASE);
}

void TerminalItem::inputMethodEvent(QInputMethodEvent *e) {
    if (!e->commitString().isEmpty()) sendText(e->commitString());
    preedit_ = e->preeditString();
    e->accept();
    update();
}

QVariant TerminalItem::inputMethodQuery(Qt::InputMethodQuery q) const {
    switch (q) {
    case Qt::ImEnabled: return true;
    case Qt::ImCursorRectangle:
        return QRectF(padding_ + info_.cursor_col * cellW_, padding_ + info_.cursor_row * cellH_, cellW_, cellH_);
    case Qt::ImFont: return fonts_[0];
    case Qt::ImSurroundingText: return QString();
    case Qt::ImCursorPosition: return 0;
    default: return QQuickItem::inputMethodQuery(q);
    }
}

void TerminalItem::focusInEvent(QFocusEvent *e) {
    QQuickItem::focusInEvent(e);
    if (term_) term_focus(term_, 1);
    cursorOn_ = true;
    ++blinkStarts_;
    blinkTimer_.start();
    update();
}

void TerminalItem::focusOutEvent(QFocusEvent *e) {
    QQuickItem::focusOutEvent(e);
    if (term_) term_focus(term_, 0);
    blinkTimer_.stop();
    update();
}

// ---- clipboard / actions --------------------------------------------------------------

void TerminalItem::sendText(const QString &text) {
    if (!term_ || text.isEmpty()) return;
    const QByteArray b = text.toUtf8();
    term_write(term_, reinterpret_cast<const uint8_t *>(b.constData()), size_t(b.size()));
}

void TerminalItem::copy() {
    if (!term_) return;
    if (char *t = term_sel_text(term_)) {
        QGuiApplication::clipboard()->setText(QString::fromUtf8(t));
        term_str_free(t);
    }
}

void TerminalItem::paste() {
    if (!term_) return;
    const QString s = QGuiApplication::clipboard()->text();
    if (!s.isEmpty()) term_paste(term_, s.toUtf8().constData());
}

void TerminalItem::selectAll() { if (term_) term_sel_all(term_); }
void TerminalItem::clearSelection() { if (term_) term_sel_clear(term_); }
void TerminalItem::scrollToBottom() { if (term_) term_scroll_local(term_, 4, 0); }
void TerminalItem::setScrollOffset(int off) { if (term_) term_scroll_local(term_, 5, qMax(0, off)); }

QString TerminalItem::screenText() const {
    QString out;
    if (cells_.empty()) return out;
    for (int r = 0; r < info_.rows; ++r) {
        QString line;
        for (int c = 0; c < info_.cols; ++c) {
            const CCell &cc = cells_[size_t(r) * info_.cols + c];
            if (cc.flags & CF_SPACER) continue;
            line += QString::fromUcs4(reinterpret_cast<const char32_t *>(&cc.ch), 1);
        }
        while (line.endsWith(' ')) line.chop(1);
        out += line + '\n';
    }
    return out;
}

// ---- mouse --------------------------------------------------------------------------

QPoint TerminalItem::cellAt(const QPointF &p, bool *right) const {
    const int cols = info_.cols > 0 ? info_.cols : cols_, rows = info_.rows > 0 ? info_.rows : rows_;
    const qreal fx = (p.x() - padding_) / cellW_, fy = (p.y() - padding_) / cellH_;
    const int c = qFloor(fx), r = qFloor(fy);
    if (right) *right = (fx - c) >= 0.5;
    return QPoint(qBound(0, c, qMax(0, cols - 1)), qBound(0, r, qMax(0, rows - 1)));
}

static int buttonIndex(Qt::MouseButton b) {
    return b == Qt::LeftButton ? 0 : b == Qt::MiddleButton ? 1 : b == Qt::RightButton ? 2 : -1;
}

void TerminalItem::mousePressEvent(QMouseEvent *e) {
    if (!term_) return;
    forceActiveFocus(Qt::MouseFocusReason);
    const bool shift = e->modifiers() & Qt::ShiftModifier;
    bool right = false;
    const QPoint cell = cellAt(e->position(), &right);
    const int b = buttonIndex(e->button());
    if (mouseTracking() && !shift && b >= 0) {
        if (term_mouse(term_, b, TACT_PRESS, cell.x(), cell.y(), modsOf(e->modifiers()))) {
            pressedButton_ = b;
            e->accept();
            return;
        }
    }
    if (e->button() == Qt::LeftButton) {
        const bool multi = clickTimer_.isValid() &&
                           clickTimer_.elapsed() < QGuiApplication::styleHints()->mouseDoubleClickInterval() && cell == lastClickCell_;
        clickCount_ = multi ? (clickCount_ % 3) + 1 : 1;
        clickTimer_.restart();
        lastClickCell_ = cell;
        updateHoverLink(cell);
        pressLink_ = hoverLink_;
        dragged_ = false;
        selecting_ = true;
        const int kind = clickCount_ == 1 ? ((e->modifiers() & Qt::AltModifier) ? 1 : 0) : clickCount_ == 2 ? 2 : 3;
        if (shift && info_.has_selection && clickCount_ == 1) term_sel_update(term_, cell.x(), cell.y(), right);
        else term_sel_start(term_, kind, cell.x(), cell.y(), right);
    } else if (e->button() == Qt::MiddleButton) {
        auto *cb = QGuiApplication::clipboard();
        const QString s = cb->text(cb->supportsSelection() ? QClipboard::Selection : QClipboard::Clipboard);
        if (!s.isEmpty()) term_paste(term_, s.toUtf8().constData());
    }
    e->accept();
}

void TerminalItem::mouseMoveEvent(QMouseEvent *e) {
    if (!term_) return;
    bool right = false;
    const QPoint cell = cellAt(e->position(), &right);
    if (pressedButton_ >= 0) {
        term_mouse(term_, pressedButton_, TACT_MOTION, cell.x(), cell.y(), modsOf(e->modifiers()));
        e->accept();
        return;
    }
    if (selecting_) {
        if (cell != lastClickCell_ || clickCount_ > 1) dragged_ = true;
        const qreal y = e->position().y();
        autoScrollDir_ = y < padding_ ? 1 : (y > height() - padding_ ? -1 : 0);
        autoScrollCell_ = cell;
        if (autoScrollDir_ && !scrollTimer_.isActive()) scrollTimer_.start();
        if (!autoScrollDir_) scrollTimer_.stop();
        term_sel_update(term_, cell.x(), cell.y(), right);
        e->accept();
    }
}

void TerminalItem::mouseReleaseEvent(QMouseEvent *e) {
    if (!term_) return;
    if (pressedButton_ >= 0) {
        const QPoint cell = cellAt(e->position());
        term_mouse(term_, pressedButton_, TACT_RELEASE, cell.x(), cell.y(), modsOf(e->modifiers()));
        pressedButton_ = -1;
        e->accept();
        return;
    }
    if (selecting_ && e->button() == Qt::LeftButton) {
        endSelectionDrag();
        if (clickCount_ == 1 && !dragged_) {
            term_sel_clear(term_);
            updateHoverLink(cellAt(e->position()));
            if (pressLink_.valid && hoverLink_.valid && pressLink_.url == hoverLink_.url &&
                (pressLink_.url.startsWith(QLatin1String("http://")) || pressLink_.url.startsWith(QLatin1String("https://"))))
                QDesktopServices::openUrl(QUrl(pressLink_.url));
        } else if (char *t = term_sel_text(term_)) { // X11 primary selection
            auto *cb = QGuiApplication::clipboard();
            if (cb->supportsSelection()) cb->setText(QString::fromUtf8(t), QClipboard::Selection);
            term_str_free(t);
        }
        e->accept();
    }
}

void TerminalItem::endSelectionDrag() {
    selecting_ = false;
    autoScrollDir_ = 0;
    scrollTimer_.stop();
}

void TerminalItem::autoScrollTick() {
    if (!term_ || !selecting_ || !autoScrollDir_) { scrollTimer_.stop(); return; }
    term_scroll_local(term_, 0, autoScrollDir_);
    const int row = autoScrollDir_ > 0 ? 0 : qMax(0, info_.rows - 1);
    term_sel_update(term_, autoScrollCell_.x(), row, autoScrollDir_ < 0);
}

void TerminalItem::hoverMoveEvent(QHoverEvent *e) {
    if (!term_) return;
    const QPoint cell = cellAt(e->position());
    if (mouseTracking() && !(e->modifiers() & Qt::ShiftModifier)) {
        term_mouse(term_, 3, TACT_MOTION, cell.x(), cell.y(), modsOf(e->modifiers()));
        setCursor(Qt::ArrowCursor);
        clearHoverLink();
        return;
    }
    updateHoverLink(cell);
}

void TerminalItem::hoverLeaveEvent(QHoverEvent *) { clearHoverLink(); hoverCell_ = QPoint(-1, -1); }

void TerminalItem::updateHoverLink(const QPoint &cell) {
    if (!term_) return;
    if (cell == hoverCell_ && hoverLink_.valid) return;
    if (cell == hoverCell_ && !hoverLink_.valid && !mouseTracking()) return;
    hoverCell_ = cell;
    int32_t r[4];
    char *u = mouseTracking() ? nullptr : term_link_at(term_, cell.x(), cell.y(), r);
    const bool was = hoverLink_.valid;
    if (u) {
        hoverLink_ = {QString::fromUtf8(u), r[0], r[1], r[2], r[3], true};
        term_str_free(u);
        setCursor(Qt::PointingHandCursor);
        update();
    } else {
        hoverLink_ = Link{};
        setCursor(mouseTracking() ? Qt::ArrowCursor : Qt::IBeamCursor);
        if (was) update();
    }
}

void TerminalItem::clearHoverLink() {
    if (!hoverLink_.valid) return;
    hoverLink_ = Link{};
    hoverCell_ = QPoint(-1, -1);
    setCursor(mouseTracking() ? Qt::ArrowCursor : Qt::IBeamCursor);
    update();
}

void TerminalItem::wheelEvent(QWheelEvent *e) {
    if (!term_) return;
    const qreal dy = !e->pixelDelta().isNull() ? e->pixelDelta().y() / cellH_ : e->angleDelta().y() / 120.0 * 3.0;
    wheelAcc_ += dy;
    const int n = int(wheelAcc_);
    wheelAcc_ -= n;
    if (n) {
        const QPoint cell = cellAt(e->position());
        term_scroll(term_, n, cell.x(), cell.y(), modsOf(e->modifiers()));
    }
    e->accept();
}

QString TerminalItem::debugInfo() const {
    return QString("cursor=(%1,%2) shape=%3 blink=%4 cols=%5 rows=%6 activeFocus=%7 winActive=%8 cursorOn=%9 mode=%10 timerActive=%11 interval=%12 fires=%17 starts=%18 refreshes=%13 paints=%14 avgPaintMs=%15 avgSnapMs=%16")
        .arg(info_.cursor_col).arg(info_.cursor_row).arg(info_.cursor_shape).arg(info_.cursor_blink)
        .arg(info_.cols).arg(info_.rows).arg(hasActiveFocus()).arg(window() && window()->isActive()).arg(cursorOn_).arg(info_.mode).arg(blinkTimer_.isActive()).arg(blinkTimer_.interval()).arg(refreshCount_).arg(paintCount_).arg(paintCount_ ? paintNs_ / 1e6 / paintCount_ : 0.0, 0, 'f', 2).arg(refreshCount_ ? snapNs_ / 1e6 / refreshCount_ : 0.0, 0, 'f', 3).arg(blinkFires_).arg(blinkStarts_);
}
