#pragma once
#include <QColor>
#include <QElapsedTimer>
#include <QFont>
#include <QPoint>
#include <QQuickPaintedItem>
#include <QStringList>
#include <QTimer>
#include <QVariantList>
#include <QtQml/qqmlregistration.h>
#include <atomic>
#include <vector>
#include "termcore.h"

/// Native terminal view. All emulation lives in Rust (termcore); this item only paints the
/// grid snapshot and translates Qt input events.
class TerminalItem : public QQuickPaintedItem {
    Q_OBJECT
    QML_NAMED_ELEMENT(Terminal)
    Q_PROPERTY(QString program MEMBER program_ NOTIFY configChanged)
    Q_PROPERTY(QStringList arguments MEMBER arguments_ NOTIFY configChanged)
    Q_PROPERTY(QString workingDirectory MEMBER cwd_ NOTIFY configChanged)
    Q_PROPERTY(bool autoStart MEMBER autoStart_)
    Q_PROPERTY(int scrollback MEMBER scrollback_)
    Q_PROPERTY(QString fontFamily READ fontFamily WRITE setFontFamily NOTIFY fontChanged)
    Q_PROPERTY(int fontPixelSize READ fontPixelSize WRITE setFontPixelSize NOTIFY fontChanged)
    Q_PROPERTY(int padding READ padding WRITE setPadding NOTIFY fontChanged)
    Q_PROPERTY(QColor foreground READ foreground WRITE setForeground NOTIFY themeChanged)
    Q_PROPERTY(QColor background READ background WRITE setBackground NOTIFY themeChanged)
    Q_PROPERTY(QColor cursorColor READ cursorColor WRITE setCursorColor NOTIFY themeChanged)
    Q_PROPERTY(QColor selectionColor READ selectionColor WRITE setSelectionColor NOTIFY themeChanged)
    Q_PROPERTY(QVariantList palette READ palette WRITE setPalette NOTIFY themeChanged)
    Q_PROPERTY(bool cursorBlink MEMBER cursorBlink_ NOTIFY configChanged)
    Q_PROPERTY(QString title READ title NOTIFY titleChanged)
    Q_PROPERTY(bool running READ running NOTIFY runningChanged)
    Q_PROPERTY(int exitCode READ exitCode NOTIFY runningChanged)
    Q_PROPERTY(int columns READ columns NOTIFY sizeChanged)
    Q_PROPERTY(int rows READ rows NOTIFY sizeChanged)
    Q_PROPERTY(int historySize READ historySize NOTIFY scrollChanged)
    Q_PROPERTY(int scrollOffset READ scrollOffset WRITE setScrollOffset NOTIFY scrollChanged)
    Q_PROPERTY(bool mouseTracking READ mouseTracking NOTIFY scrollChanged)
    Q_PROPERTY(bool hasSelection READ hasSelection NOTIFY selectionChanged)

public:
    explicit TerminalItem(QQuickItem *parent = nullptr);
    ~TerminalItem() override;

    QString fontFamily() const { return family_; }
    void setFontFamily(const QString &f);
    int fontPixelSize() const { return pixelSize_; }
    void setFontPixelSize(int px);
    int padding() const { return padding_; }
    void setPadding(int p);
    QColor foreground() const { return fg_; }
    void setForeground(const QColor &c);
    QColor background() const { return bg_; }
    void setBackground(const QColor &c);
    QColor cursorColor() const { return cursorColor_; }
    void setCursorColor(const QColor &c);
    QColor selectionColor() const { return selColor_; }
    void setSelectionColor(const QColor &c);
    QVariantList palette() const;
    void setPalette(const QVariantList &p);
    QString title() const { return title_; }
    bool running() const { return term_ && term_alive(term_); }
    int exitCode() const { return term_ ? term_exit_code(term_) : 0; }
    int columns() const { return cols_; }
    int rows() const { return rows_; }
    int historySize() const { return info_.history_size; }
    int scrollOffset() const { return info_.display_offset; }
    void setScrollOffset(int off);
    bool mouseTracking() const { return info_.mode & MODE_MOUSE; }
    bool hasSelection() const { return info_.has_selection; }

    Q_INVOKABLE void start();
    Q_INVOKABLE void stop();
    Q_INVOKABLE void sendText(const QString &text);
    Q_INVOKABLE void copy();
    Q_INVOKABLE void paste();
    Q_INVOKABLE void selectAll();
    Q_INVOKABLE void clearSelection();
    Q_INVOKABLE void scrollToBottom();
    /// Visible grid as plain text (accessibility, tests, "copy screen").
    Q_INVOKABLE QString screenText() const;
    Q_INVOKABLE QString debugInfo() const;

    void paint(QPainter *painter) override;

signals:
    void configChanged();
    void fontChanged();
    void themeChanged();
    void titleChanged();
    void runningChanged();
    void sizeChanged();
    void scrollChanged();
    void selectionChanged();
    void bell();
    void exited(int code);

protected:
    void componentComplete() override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;
    void keyPressEvent(QKeyEvent *e) override;
    void keyReleaseEvent(QKeyEvent *e) override;
    void inputMethodEvent(QInputMethodEvent *e) override;
    QVariant inputMethodQuery(Qt::InputMethodQuery q) const override;
    void focusInEvent(QFocusEvent *e) override;
    void focusOutEvent(QFocusEvent *e) override;
    void mousePressEvent(QMouseEvent *e) override;
    void mouseMoveEvent(QMouseEvent *e) override;
    void mouseReleaseEvent(QMouseEvent *e) override;
    void hoverMoveEvent(QHoverEvent *e) override;
    void hoverLeaveEvent(QHoverEvent *e) override;
    void wheelEvent(QWheelEvent *e) override;

private:
    static void onCoreEvent(void *user, int kind);
    void handleEvent(int kind);
    void refresh();
    void scheduleRefresh();
    void updateMetrics();
    void fit();
    void applyTheme();
    void maybeStart();
    void sendKey(QKeyEvent *e, int event);
    QPoint cellAt(const QPointF &p, bool *rightHalf = nullptr) const;
    void updateHoverLink(const QPoint &cell);
    void clearHoverLink();
    void autoScrollTick();
    void endSelectionDrag();
    static uint32_t modsOf(Qt::KeyboardModifiers m);
    QFont fontFor(uint32_t flags) const;

    mutable quint64 blinkFires_ = 0, blinkStarts_ = 0, refreshCount_ = 0, paintCount_ = 0, paintNs_ = 0, snapNs_ = 0;
    Core *term_ = nullptr;
    std::atomic<bool> wakePending_{false};
    QElapsedTimer lastRefresh_;
    std::vector<CCell> cells_;
    CInfo info_{};
    int cols_ = 0, rows_ = 0;

    // config
    QString program_, cwd_;
    QStringList arguments_;
    bool autoStart_ = true;
    int scrollback_ = 10000;
    QString family_ = QStringLiteral("monospace");
    int pixelSize_ = 15;
    int padding_ = 4;
    QColor fg_{0xff, 0xff, 0xff}, bg_{0, 0, 0}, cursorColor_{0xff, 0xff, 0xff}, selColor_{0x4d, 0x4d, 0x4d};
    QList<QColor> palette_;
    bool cursorBlink_ = false;

    // metrics
    QFont fonts_[4];
    qreal cellW_ = 8, cellH_ = 16, ascent_ = 12;

    // state
    QString title_;
    QString preedit_;
    bool cursorOn_ = true;
    QTimer blinkTimer_, scrollTimer_;
    int autoScrollDir_ = 0;
    QPoint autoScrollCell_;

    // mouse
    int pressedButton_ = -1;       // forwarded to app
    bool selecting_ = false, dragged_ = false;
    int clickCount_ = 0;
    QElapsedTimer clickTimer_;
    QPoint lastClickCell_{-1, -1};
    qreal wheelAcc_ = 0;
    struct Link { QString url; int c0 = 0, r0 = 0, c1 = 0, r1 = 0; bool valid = false; };
    Link hoverLink_, pressLink_;
    QPoint hoverCell_{-1, -1};
};
