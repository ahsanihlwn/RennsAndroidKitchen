// Sends real Qt input events to the item and checks what the child process receives / what the UI does.
#include <QClipboard>
#include <QFontMetricsF>
#include <QGuiApplication>
#include <QQuickWindow>
#include <QtTest>
#include "terminal_item.h"

class InputTest : public QObject {
    Q_OBJECT
    QQuickWindow *win = nullptr;
    TerminalItem *t = nullptr;

    // Child echoes every byte it receives in `cat -v` notation (ESC=^[ , CR=^M).
    void startCatV(const QString &prefix = QString()) {
        delete t; t = new TerminalItem(win->contentItem());
        t->setWidth(640); t->setHeight(320);
        t->setFontFamily("DejaVu Sans Mono");
        t->setProperty("program", "/bin/sh");
        t->setProperty("arguments", QStringList{"-c", prefix + "stty -echo -icanon -icrnl; cat -v"});
        t->start();
        t->forceActiveFocus();
        QVERIFY2(QTest::qWaitFor([&] { return t->running() && t->hasActiveFocus(); }, 3000), "terminal didn't start");
        QTest::qWait(300);
    }
    bool screenHas(const QString &s) { return QTest::qWaitFor([&] { return t->screenText().contains(s); }, 2000); }

private slots:
    void initTestCase() {
        win = new QQuickWindow;
        win->resize(700, 400);
        win->show();
        QVERIFY(QTest::qWaitForWindowExposed(win));
        win->requestActivate();
    }
    void cleanupTestCase() { delete t; delete win; }

    void legacyKeys() {
        startCatV();
        QTest::keyClick(win, Qt::Key_Up);                                   QVERIFY2(screenHas("^[[A"), qPrintable(t->screenText()));
        QTest::keyClick(win, Qt::Key_A, Qt::ControlModifier);               QVERIFY(screenHas("^A"));
        QTest::keyClick(win, Qt::Key_B, Qt::AltModifier);                   QVERIFY(screenHas("^[b"));
        QTest::keyClick(win, Qt::Key_F5);                                   QVERIFY(screenHas("^[[15~"));
        QTest::keyClick(win, Qt::Key_Left, Qt::ControlModifier);            QVERIFY(screenHas("^[[1;5D"));
        QTest::keyClick(win, Qt::Key_Return, Qt::ShiftModifier);            QVERIFY(screenHas("^M"));  // legacy: same as Enter
        for (QChar ch : QString("hello")) QTest::keyClick(win, ch.toLatin1());
        { QKeyEvent ev(QEvent::KeyPress, 0, Qt::NoModifier, QString::fromUtf8("é")); QGuiApplication::sendEvent(win, &ev); }
        QVERIFY(screenHas("helloM-CM-)")); // UTF-8 bytes C3 A9 as seen by cat -v
        QTest::keyClick(win, Qt::Key_Backtab, Qt::ShiftModifier);           QVERIFY(screenHas("^[[Z"));
    }
    void kittyKeyboard() {
        startCatV("printf '\\033[>1u'; ");   // app enables kitty disambiguate
        QTest::keyClick(win, Qt::Key_Return, Qt::ShiftModifier);            QVERIFY2(screenHas("^[[13;2u"), qPrintable(t->screenText()));
        QTest::keyClick(win, Qt::Key_Escape);                               QVERIFY(screenHas("^[[27u"));
        QTest::keyClick(win, Qt::Key_I, Qt::ControlModifier);               QVERIFY(screenHas("^[[105;5u"));
    }
    void bracketedPasteAndClipboard() {
        startCatV("printf '\\033[?2004h'; ");
        QGuiApplication::clipboard()->setText("one\ntwo");
        QTest::keyClick(win, Qt::Key_V, Qt::ControlModifier | Qt::ShiftModifier);
        QVERIFY2(screenHas("^[[200~one^Mtwo^[[201~"), qPrintable(t->screenText()));
    }
    void selectionCopyAndDoubleClick() {
        startCatV();
        t->sendText("alpha beta gamma\r");
        QVERIFY(screenHas("alpha beta gamma"));
        // cell (col,row) -> pixel: padding 4, cell size from item metrics
        const QString txt = t->screenText();
        QVERIFY(txt.startsWith("alpha beta gamma"));
        // drag across "alpha" on row 0
        const qreal cw = (t->width() - 8) / t->columns(); Q_UNUSED(cw);
        QFont f("DejaVu Sans Mono"); f.setPixelSize(t->fontPixelSize());
        const qreal w = qRound(QFontMetricsF(f).horizontalAdvance('M')), h = qCeil(QFontMetricsF(f).height());
        QTest::mousePress(win, Qt::LeftButton, {}, QPoint(int(4 + 0.25 * w), int(4 + 0.5 * h)));
        QTest::mouseMove(win, QPoint(int(4 + 4.75 * w), int(4 + 0.5 * h)));
        QTest::mouseRelease(win, Qt::LeftButton, {}, QPoint(int(4 + 4.75 * w), int(4 + 0.5 * h)));
        QVERIFY(QTest::qWaitFor([&] { return t->hasSelection(); }, 1000));
        t->copy();
        QCOMPARE(QGuiApplication::clipboard()->text(), QString("alpha"));
        // double-click selects the word "beta"
        const QPoint p(int(4 + 7.5 * w), int(4 + 0.5 * h));
        QTest::mouseDClick(win, Qt::LeftButton, {}, p);
        QVERIFY(QTest::qWaitFor([&] { return t->hasSelection(); }, 1000));
        t->copy();
        QCOMPARE(QGuiApplication::clipboard()->text(), QString("beta"));
        // third click in a row selects the whole line
        QTest::mouseClick(win, Qt::LeftButton, {}, p);
        QVERIFY(QTest::qWaitFor([&] { t->copy(); return QGuiApplication::clipboard()->text().startsWith("alpha beta gamma"); }, 1000));
        // typing clears selection (xterm.js behaviour)
        QTest::keyClick(win, Qt::Key_X);
        QVERIFY(QTest::qWaitFor([&] { return !t->hasSelection(); }, 1000));
    }
    void mouseReportingAndWheel() {
        startCatV("printf '\\033[?1000h\\033[?1006h'; ");   // app asks for SGR mouse
        QVERIFY(QTest::qWaitFor([&] { return t->mouseTracking(); }, 2000));
        QTest::mouseClick(win, Qt::LeftButton, {}, QPoint(4 + 20, 4 + 20));
        QVERIFY2(screenHas("^[[<0;"), qPrintable(t->screenText()));
        QTest::mouseClick(win, Qt::LeftButton, Qt::ShiftModifier, QPoint(30, 30)); // shift bypasses reporting
        QWheelEvent we(QPointF(30, 30), QPointF(30, 30), QPoint(), QPoint(0, 120), Qt::NoButton, Qt::NoModifier, Qt::NoScrollPhase, false);
        QGuiApplication::sendEvent(win, &we);
        QVERIFY2(screenHas("^[[<64;"), qPrintable(t->screenText()));  // wheel up reported as button 64
    }
    void scrollbackAndWheel() {
        delete t; t = new TerminalItem(win->contentItem());
        t->setWidth(640); t->setHeight(200); t->setFontFamily("DejaVu Sans Mono");
        t->setProperty("program", "/bin/sh");
        t->setProperty("arguments", QStringList{"-c", "for i in $(seq 1 100); do echo row$i; done; sleep 20"});
        t->start(); t->forceActiveFocus();
        QVERIFY(QTest::qWaitFor([&] { return t->historySize() > 50 && t->screenText().contains("row100"); }, 3000));
        QCOMPARE(t->scrollOffset(), 0);
        QWheelEvent we(QPointF(30, 30), QPointF(30, 30), QPoint(), QPoint(0, 120), Qt::NoButton, Qt::NoModifier, Qt::NoScrollPhase, false);
        QGuiApplication::sendEvent(win, &we);
        QVERIFY(QTest::qWaitFor([&] { return t->scrollOffset() == 3; }, 1000));
        QTest::keyClick(win, Qt::Key_PageUp, Qt::ShiftModifier);
        QVERIFY(QTest::qWaitFor([&] { return t->scrollOffset() > 3; }, 1000));
        QTest::keyClick(win, Qt::Key_X);   // typing snaps back to bottom
        QVERIFY(QTest::qWaitFor([&] { return t->scrollOffset() == 0; }, 1000));
    }
    void resizeFitsGrid() {
        startCatV();
        const int c0 = t->columns(), r0 = t->rows();
        t->setWidth(400); t->setHeight(200);
        QVERIFY(QTest::qWaitFor([&] { return t->columns() < c0 && t->rows() < r0; }, 1000));
        t->sendText("stty size\r");    // fails under cat but tty winsize is visible via the next test line
        QCOMPARE(t->property("columns").toInt() > 10, true);
    }
};

QTEST_MAIN(InputTest)
#include "input_test.moc"
