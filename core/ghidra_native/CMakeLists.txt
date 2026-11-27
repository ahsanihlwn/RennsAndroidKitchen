# Build of Ghidra's C++ decompiler and SLEIGH compiler (Apache-2.0, see LICENSE/NOTICE).
# Excludes the GPL libbfd loader and all Ghidra-Java pipe glue. RAK drives the
# console decompiler (`decomp_cli`) over a <binaryimage> XML description.
cmake_minimum_required(VERSION 3.20)
project(rak_ghidra_native LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CPP "${CMAKE_CURRENT_LIST_DIR}/cpp")
set(ZLIB_DIR "${CMAKE_CURRENT_LIST_DIR}/zlib")

add_compile_definitions(LOCAL_ZLIB _CRT_SECURE_NO_WARNINGS NOMINMAX)
if(WIN32)
    add_compile_definitions(_WINDOWS)
endif()

# Ghidra's bundled zlib omits crc32.c; the decompiler only needs gzip-free
# deflate/inflate, so a tiny bitwise crc32 satisfies the linker.
add_library(ghidra_zlib STATIC
    ${ZLIB_DIR}/adler32.c ${ZLIB_DIR}/deflate.c ${ZLIB_DIR}/inffast.c
    ${ZLIB_DIR}/inflate.c ${ZLIB_DIR}/inftrees.c ${ZLIB_DIR}/trees.c ${ZLIB_DIR}/zutil.c
    ${CMAKE_CURRENT_LIST_DIR}/zlib_crc32_shim.c
)
target_include_directories(ghidra_zlib PUBLIC ${ZLIB_DIR})

set(CORE_SRCS
    ${CPP}/xml.cc ${CPP}/marshal.cc ${CPP}/space.cc ${CPP}/float.cc ${CPP}/address.cc
    ${CPP}/pcoderaw.cc ${CPP}/translate.cc ${CPP}/opcodes.cc ${CPP}/metatype.cc ${CPP}/globalcontext.cc
)
set(SLEIGH_SRCS
    ${CPP}/sleigh.cc ${CPP}/pcodeparse.cc ${CPP}/pcodecompile.cc ${CPP}/sleighbase.cc
    ${CPP}/slghsymbol.cc ${CPP}/slghpatexpress.cc ${CPP}/slghpattern.cc ${CPP}/semantics.cc
    ${CPP}/context.cc ${CPP}/slaformat.cc ${CPP}/compression.cc ${CPP}/filemanage.cc
)
add_executable(sleigh_comp ${CORE_SRCS} ${SLEIGH_SRCS}
    ${CPP}/slgh_compile.cc ${CPP}/slghparse.cc ${CPP}/slghscan.cc)
target_include_directories(sleigh_comp PRIVATE ${CPP} ${ZLIB_DIR})
target_link_libraries(sleigh_comp PRIVATE ghidra_zlib)

file(GLOB DECOMP_SRCS "${CPP}/*.cc")
list(FILTER DECOMP_SRCS EXCLUDE REGEX ".*/(slgh_compile|slghparse|slghscan|rulecompile)\\.cc$")
add_executable(decomp_cli ${DECOMP_SRCS})
target_include_directories(decomp_cli PRIVATE ${CPP} ${ZLIB_DIR})
target_link_libraries(decomp_cli PRIVATE ghidra_zlib)
