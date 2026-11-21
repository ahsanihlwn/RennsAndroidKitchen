#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickWindow>
#include <QTimer>
#include <QtQml>
#include <QKeyEvent>
#include <QDateTime>
#include "terminal_item.h"

int main(int argc, char *argv[]) {
    QGuiApplication app(argc, argv);
    qmlRegisterType<TerminalItem>("QtTerm", 1, 0, "Terminal");

    // usage: qtterm [program [args...]]   (default: your shell)
    const QStringList args = app.arguments().mid(1);
    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty("cmdProgram", args.value(0));
    engine.rootContext()->setContextProperty("cmdArguments", args.mid(1));
    engine.rootContext()->setContextProperty("fontOverride", qEnvironmentVariable("QTTERM_FONT", "monospace"));
    engine.load(QUrl(QStringLiteral("qrc:/qml/Main.qml")));
    if (engine.rootObjects().isEmpty()) return 1;

    // Test helper: QTTERM_SHOT=/path.png [QTTERM_SHOT_MS=1500] saves a screenshot and exits.
    if (qEnvironmentVariableIsSet("QTTERM_SHOT")) {
        const QString path = qEnvironmentVariable("QTTERM_SHOT");
        const int ms = qEnvironmentVariableIntValue("QTTERM_SHOT_MS") ? qEnvironmentVariableIntValue("QTTERM_SHOT_MS") : 1500;
        auto *win = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
        QTimer::singleShot(ms, &app, [win, path, &app] {
            win->grabWindow().save(path);
            app.quit();
        });
    }
    // Test helper: QTTERM_SCRIPT="wait:20000,shot:/tmp/a.png,ctrl+p,wait:2000,shot:/tmp/b.png,esc,text:hello,enter,quit"
    if (qEnvironmentVariableIsSet("QTTERM_SCRIPT")) {
        auto *win = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
        const QStringList steps = qEnvironmentVariable("QTTERM_SCRIPT").split(',');
        int t = 0;
        auto key = [win](int k, Qt::KeyboardModifiers m, const QString &txt) {
            QKeyEvent p(QEvent::KeyPress, k, m, txt), r(QEvent::KeyRelease, k, m, txt);
            QCoreApplication::sendEvent(win, &p);
            QCoreApplication::sendEvent(win, &r);
        };
        for (const QString &st : steps) {
            if (st.startsWith("wait:")) { t += st.mid(5).toInt(); continue; }
            QTimer::singleShot(t, Qt::PreciseTimer, &app, [=, &app] {
                if (st.startsWith("shot:")) { qInfo().noquote() << "SHOT" << QDateTime::currentMSecsSinceEpoch() % 1000000 << st.mid(5); win->grabWindow().save(st.mid(5)); qInfo().noquote() << "SHOTDONE" << QDateTime::currentMSecsSinceEpoch() % 1000000; }
                else if (st == "info") { if (auto *ti = win->findChild<TerminalItem *>()) qInfo().noquote() << "INFO" << ti->debugInfo(); }
                else if (st == "quit") app.quit();
                else if (st == "enter") key(Qt::Key_Return, {}, "\r");
                else if (st == "esc") key(Qt::Key_Escape, {}, "\x1b");
                else if (st == "tab") key(Qt::Key_Tab, {}, "\t");
                else if (st.startsWith("ctrl+")) key(st.at(5).toUpper().unicode(), Qt::ControlModifier, QString());
                else if (st.startsWith("text:")) for (QChar c : st.mid(5)) key(c.toUpper().unicode(), {}, QString(c));
            });
        }
    }
    return app.exec();
}
