cmake_minimum_required(VERSION 3.21)
project(qtterm LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Quick Qml)
find_package(Threads REQUIRED)

# --- Rust core (cargo) ---------------------------------------------------------------
# Override with -DCARGO=/path/to/cargo if the default toolchain is older than 1.88.
find_program(CARGO cargo REQUIRED HINTS /usr/lib/rust-1.89/bin $ENV{HOME}/.cargo/bin)
set(TERMCORE_TARGET_DIR ${CMAKE_BINARY_DIR}/cargo)
if (MSVC)
    set(TERMCORE_LIB ${TERMCORE_TARGET_DIR}/release/termcore.lib)
    set(PLATFORM_LIBS ws2_32 userenv ntdll bcrypt)
else()
    set(TERMCORE_LIB ${TERMCORE_TARGET_DIR}/release/libtermcore.a)
    set(PLATFORM_LIBS ${CMAKE_DL_LIBS})
endif()
get_filename_component(CARGO_BIN_DIR ${CARGO} DIRECTORY)
# Put cargo's own directory first so its matching rustc is used (distro wrappers call `rustc` from PATH).
add_custom_target(termcore_build
    COMMAND ${CMAKE_COMMAND} -E env "PATH=${CARGO_BIN_DIR}:$ENV{PATH}" CARGO_TARGET_DIR=${TERMCORE_TARGET_DIR}
            ${CARGO} build --release --manifest-path ${CMAKE_SOURCE_DIR}/core/Cargo.toml
    BYPRODUCTS ${TERMCORE_LIB}
    COMMENT "Building Rust termcore"
    USES_TERMINAL)
add_library(termcore STATIC IMPORTED GLOBAL)
set_target_properties(termcore PROPERTIES IMPORTED_LOCATION ${TERMCORE_LIB})
add_dependencies(termcore termcore_build)

# --- Qt Quick app --------------------------------------------------------------------
qt_add_executable(qtterm src/main.cpp src/terminal_item.cpp src/terminal_item.h)
qt_add_resources(qtterm "qml" PREFIX "/" FILES qml/Main.qml)
target_include_directories(qtterm PRIVATE include src)
target_link_libraries(qtterm PRIVATE Qt6::Quick Qt6::Qml Qt6::Gui termcore Threads::Threads ${PLATFORM_LIBS})

# --- QML plugin: `import QtTerm 1.0` from any QML engine (PySide6 / PyQt6 / C++) ----------
# Output: build/qml/QtTerm/{qmldir, libqttermplugin.so}  -> engine.addImportPath("build/qml")
# NOTE: a QQuickItem plugin is Qt-ABI bound: build it against the same Qt X.Y your Python binding uses.
qt_add_library(qtterm_qml SHARED)
qt_add_qml_module(qtterm_qml
    URI QtTerm
    VERSION 1.0
    PLUGIN_TARGET qttermplugin
    OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/qml/QtTerm
    SOURCES src/terminal_item.cpp src/terminal_item.h)
target_include_directories(qtterm_qml PRIVATE include src)
target_link_libraries(qtterm_qml PRIVATE Qt6::Quick Qt6::Qml Qt6::Gui termcore Threads::Threads ${PLATFORM_LIBS})
set_target_properties(qtterm_qml PROPERTIES POSITION_INDEPENDENT_CODE ON)

# --- tests (ctest) ---------------------------------------------------------------------
find_package(Qt6 COMPONENTS Test)
if(Qt6Test_FOUND)
    enable_testing()
    qt_add_executable(qtterm_input_test tests/input_test.cpp src/terminal_item.cpp src/terminal_item.h)
    target_include_directories(qtterm_input_test PRIVATE include src)
    target_link_libraries(qtterm_input_test PRIVATE Qt6::Test Qt6::Quick Qt6::Gui termcore Threads::Threads ${PLATFORM_LIBS})
    add_test(NAME input COMMAND qtterm_input_test)
    set_tests_properties(input PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;QT_QUICK_BACKEND=software")
endif()
