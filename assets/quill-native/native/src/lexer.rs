//! Small stateful lexical colorer, intentionally not a semantic parser.
//! Token kinds: keyword, string, comment, number, type, function, operator, tag.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct State {
    mode: u8, // 0 normal, 1 block comment, 2/3 Python/Swift triple quotes, 4 JS/Lua template, 5 HTML comment, 6 WAT comment, 7 Haskell comment, 8 Nim comment
    depth: u16,
    raw_hashes: u16, // Rust r###"..."###; zero means not a raw string
}

pub const LANGUAGES: &[&str] = &[
    "Plain Text",
    "Rust",
    "Python",
    "JavaScript",
    "TypeScript",
    "C",
    "C++",
    "C#",
    "Java",
    "Kotlin",
    "Swift",
    "Go",
    "Assembly",
    "Smali",
    "Lua",
    "PHP",
    "Ruby",
    "Dart",
    "JSON",
    "HTML",
    "XML",
    "CSS",
    "Shell",
    "Batch",
    "PowerShell",
    "Makefile",
    "Dockerfile",
    "CMake",
    "SQL",
    "TOML",
    "YAML",
    "INI",
    "Markdown",
    "QML",
    "Diff",
    "Perl",
    "R",
    "GraphQL",
    "Device Tree",
    "Kconfig",
    "SELinux",
    "Blueprint",
    "AIDL",
    "Groovy",
    "Zig",
    "Protobuf",
    "Objective-C",
    "WAT",
    "Scala",
    "Elixir",
    "Haskell",
    "Clojure",
    "Nim",
    "Vimscript",
];

pub fn detect(path: &str) -> &'static str {
    let name = path
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or(path)
        .to_ascii_lowercase();

    match name.as_str() {
        "makefile" | "gnumakefile" | "android.mk" | "cleanspec.mk" => return "Makefile",
        "dockerfile" | "containerfile" => return "Dockerfile",
        "cmakelists.txt" => return "CMake",
        "gemfile" | "rakefile" => return "Ruby",
        "android.bp" => return "Blueprint",
        "kconfig" | "defconfig" | ".config" => return "Kconfig",
        "build.gradle" | "settings.gradle" => return "Groovy",
        "file_contexts" | "property_contexts" | "service_contexts" | "hwservice_contexts" | "vndservice_contexts" => return "SELinux",
        "vimrc" | ".vimrc" => return "Vimscript",
        _ => {}
    }

    if name.ends_with("defconfig") {
        return "Kconfig";
    }

    match name.rsplit('.').next().unwrap_or("") {
        "rs" => "Rust",
        "py" | "pyw" => "Python",
        "js" | "jsx" | "mjs" | "cjs" => "JavaScript",
        "ts" | "tsx" => "TypeScript",
        "c" | "h" => "C",
        "cc" | "cpp" | "hpp" | "cxx" | "hxx" | "hh" => "C++",
        "cs" => "C#",
        "java" => "Java",
        "kt" | "kts" => "Kotlin",
        "swift" => "Swift",
        "go" => "Go",
        "s" | "asm" | "inc" | "nasm" | "a51" => "Assembly",
        "smali" => "Smali",
        "lua" => "Lua",
        "php" | "phtml" | "php3" | "php4" | "php5" => "PHP",
        "rb" | "rake" | "gemspec" => "Ruby",
        "dart" => "Dart",
        "json" | "jsonc" => "JSON",
        "html" | "htm" => "HTML",
        "xml" | "svg" | "xsl" | "xslt" | "xsd" | "plist" => "XML",
        "css" | "scss" | "less" | "qss" => "CSS",
        "sh" | "bash" | "zsh" | "fish" | "ksh" => "Shell",
        "bat" | "cmd" => "Batch",
        "ps1" | "psm1" | "psd1" => "PowerShell",
        "mk" | "mak" => "Makefile",
        "cmake" => "CMake",
        "sql" => "SQL",
        "toml" => "TOML",
        "yaml" | "yml" => "YAML",
        "ini" | "cfg" | "conf" | "prop" | "properties" | "inf" => "INI",
        "md" | "markdown" => "Markdown",
        "qml" => "QML",
        "diff" | "patch" => "Diff",
        "pl" | "pm" | "t" => "Perl",
        "r" => "R",
        "graphql" | "gql" => "GraphQL",
        "dts" | "dtsi" => "Device Tree",
        "kconfig" => "Kconfig",
        "te" | "cil" => "SELinux",
        "bp" => "Blueprint",
        "aidl" | "hal" => "AIDL",
        "gradle" | "groovy" | "gvy" => "Groovy",
        "zig" | "zon" => "Zig",
        "proto" => "Protobuf",
        "m" | "mm" => "Objective-C",
        "wat" | "wast" => "WAT",
        "scala" | "sc" => "Scala",
        "ex" | "exs" | "erl" | "hrl" => "Elixir",
        "hs" | "lhs" => "Haskell",
        "clj" | "cljs" | "cljc" | "edn" => "Clojure",
        "nim" | "nims" | "nimble" => "Nim",
        "vim" => "Vimscript",
        _ => "Plain Text",
    }
}

fn keyword(language: &str, word: &str) -> bool {
    let keywords = match language {
        "Rust" => "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while union",
        "Python" => "and as assert async await break case class continue def del elif else except False finally for from global if import in is lambda match None nonlocal not or pass raise return True try while with yield",
        "JavaScript" | "TypeScript" | "QML" => "abstract as async await break case catch class const continue debugger default delete do else enum export extends false finally for from function get if implements import in instanceof interface let new null of package private property protected public readonly required return set signal static super switch this throw true try typeof undefined var void while with yield id on",
        "C" | "C++" | "C#" | "Java" => "abstract alignas auto bool boolean break case catch char class const constexpr continue default delete do double else enum explicit extern false final finally float for friend if import include inline int interface long namespace new null nullptr operator override package private protected public register return short signed sizeof static struct super switch template this throw true try typedef typename union unsigned using virtual void volatile while",
        "Kotlin" => "abstract actual annotation as break by catch class companion const constructor continue crossinline data delegate do dynamic else enum expect external false final finally for fun get if import in infix init inline inner interface internal is late-init noinline null object open operator out override package private protected public receiver reified return sealed set super suspend tailrec this throw true try typealias val var vararg when where while",
        "Swift" => "actor Any as associatedtype async await break case catch class continue default defer deinit do else enum extension fallthrough false fileprivate final for func get guard if import in init inout internal is isolated let macro mutating nil nonisolated open operator override package precedencegroup private protocol public repeat required rethrows return self Self set some static struct subscript super switch throw throws true try typealias var where while willSet didSet",
        "Go" => "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var true false nil",
        "Assembly" => "mov movzx movsx push pop add adc sub sbc mul imul div idiv inc dec lea nop call ret jmp je jz jne jnz ja jae jb jbe jg jge jl jle js jns jc jnc cmp test and or xor not shl shr sal sar rol ror rcl rcr int syscall sysenter sysexit leave enter loop loope loopne clc stc cld std cli sti hlt pause cpuid rdtsc lock bsf bsr bt btc btr bts cmpxchg xchg ldr str ldrb strb ldrh strh ldrd strd b bl bx blx cbz cbnz adr adrp svc msr mrs wfi wfe sev dmb dsb isb eor orr bic mvn lsl lsr asr rrx rsb cmn tst teq section global globl extern equ align byte word dword qword ascii asciiz space org db dw dd dq resb resw resd resq text data bss rax rbx rcx rdx rsi rdi rbp rsp r8 r9 r10 r11 r12 r13 r14 r15 eax ebx ecx edx esi edi ebp esp ax bx cx dx sp bp si di al bl cl dl ah bh ch dh r0 r1 r2 r3 r4 r5 r6 r7 r8 r9 r10 r11 r12 r13 r14 r15 lr pc x0 x1 x2 x3 x4 x5 x6 x7 x8 x9 x10 x11 x12 x13 x14 x15 x16 x17 x18 x19 x20 x21 x22 x23 x24 x25 x26 x27 x28 x29 x30 w0 w1 w2 w3 w4 w5 w6 w7 w8 w9 w10 w11 w12 w13 w14 w15 w16 w17 w18 w19 w20 w21 w22 w23 w24 w25 w26 w27 w28 w29 w30",
        "Smali" => ".class .super .source .field .subannotation .annotation .enum .method .end .registers .locals .param .prologue .line .catch .catchall .restart .packed-switch .sparse-switch .array-data public private protected static final synchronized bridge varargs native abstract strictfp synthetic synthetic-access constructor declared-synchronized nop move move/from16 move/16 move-wide move-wide/from16 move-wide/16 move-object move-object/from16 move-object/16 move-result move-result-wide move-result-object move-exception return-void return return-wide return-object const/4 const/16 const const/high16 const-wide/16 const-wide/32 const-wide const-wide/high16 const-string const-string/jumbo const-class monitor-enter monitor-exit check-cast instance-of array-length new-instance new-array filled-new-array filled-new-array/range fill-array-data throw goto goto/16 goto/32 packed-switch sparse-switch cmpl-float cmpg-float cmpl-double cmpg-double cmp-long if-eq if-ne if-lt if-ge if-gt if-le if-eqz if-nez if-ltz if-gez if-gtz if-lez aget aget-wide aget-object aget-boolean aget-byte aget-char aget-short aput aput-wide aput-object aput-boolean aput-byte aput-char aput-short iget iget-wide iget-object iget-boolean iget-byte iget-char iget-short iput iput-wide iput-object iput-boolean iput-byte iput-char iput-short sget sget-wide sget-object sget-boolean sget-byte sget-char sget-short sput sput-wide sput-object sput-boolean sput-byte sput-char sput-short invoke-virtual invoke-super invoke-direct invoke-static invoke-interface invoke-virtual/range invoke-super/range invoke-direct/range invoke-static/range invoke-interface/range invoke-custom invoke-custom/range invoke-polymorphic invoke-polymorphic/range neg-int not-int neg-long not-long neg-float neg-double int-to-long int-to-float int-to-double long-to-int long-to-float long-to-double float-to-int float-to-long float-to-double double-to-int double-to-long double-to-float int-to-byte int-to-char int-to-short add-int sub-int mul-int div-int rem-int and-int or-int xor-int shl-int shr-int ushr-int rsub-int add-float sub-float mul-float div-float rem-float add-double sub-double mul-double div-double rem-double add-int/2addr sub-int/2addr mul-int/2addr div-int/2addr rem-int/2addr and-int/2addr or-int/2addr xor-int/2addr shl-int/2addr shr-int/2addr ushr-int/2addr add-float/2addr sub-float/2addr mul-float/2addr div-float/2addr rem-float/2addr add-double/2addr sub-double/2addr mul-double/2addr div-double/2addr rem-double/2addr add-int/lit16 rsub-int/lit16 mul-int/lit16 div-int/lit16 rem-int/lit16 and-int/lit16 or-int/lit16 xor-int/lit16 add-int/lit8 rsub-int/lit8 mul-int/lit8 div-int/lit8 rem-int/lit8 and-int/lit8 or-int/lit8 xor-int/lit8 shl-int/lit8 shr-int/lit8 ushr-int/lit8 true false null",
        "Lua" => "and break do else elseif end false for function goto if in local nil not or repeat return then true until while",
        "PHP" => "abstract and array as break callable case catch class clone const continue declare default die do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile eval exit extends final finally fn for foreach function global goto if implements include include_once instanceof insteadof interface isset list match namespace new or print private protected public readonly require require_once return static switch throw trait try use var while xor yield true false null",
        "Ruby" => "alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield __FILE__ __LINE__",
        "Dart" => "abstract as assert async await base break case catch class const continue covariant default deferred do dynamic else enum export extends extension external factory false final finally for function get hide if implements import in interface is late library mixin new null of on operator part required rethrow return sealed set show static super switch sync this throw true try typedef var void when while with yield",
        "Makefile" => "include define endef ifeq ifneq ifdef ifndef else endif override export unexport vpath load .PHONY .DEFAULT .PRECIOUS .IGNORE .SILENT .SECONDARY .SECONDEXPANSION .INTERMEDIATE .LOW_RESOLUTION_TIME .EXPORT_ALL_VARIABLES .NOTPARALLEL .ONESHELL .POSIX",
        "Dockerfile" => "FROM MAINTAINER RUN CMD LABEL EXPOSE ENV ADD COPY ENTRYPOINT VOLUME USER WORKDIR ARG ONBUILD STOPSIGNAL HEALTHCHECK SHELL AS",
        "CMake" => "cmake_minimum_required project add_executable add_library set unset find_package include_directories target_link_libraries target_include_directories target_compile_options target_sources target_compile_definitions add_subdirectory if elseif else endif while endwhile foreach endforeach return function endfunction macro endmacro message option set_target_properties get_target_property install enable_testing add_test include list string configure_file file add_custom_command add_custom_target execute_process find_library find_path find_program find_file true false yes no on off",
        "Batch" => "echo off on if else goto call set setlocal endlocal exit pause for in do shift errorlevel rem not exist defined start cd chdir md mkdir rd rmdir copy xcopy robocopy del erase type ren rename find findstr cls title choice",
        "PowerShell" => "function filter param begin process end dynamicparam if else elseif switch while default for foreach in do until break continue return trap throw class enum using hidden static try catch finally workflow parallel sequence inlinescript exit",
        "Perl" => "my our local sub if unless elsif else while until for foreach do continue last next redo goto return die warn eval require use no package strict warnings eq ne lt gt le ge cmp and or not xor true false",
        "R" => "if else repeat while function for in next break TRUE FALSE NULL NA Inf NaN",
        "GraphQL" => "type query mutation subscription schema interface union enum input scalar fragment on directive true false null extend implements",
        "Device Tree" => "/dts-v1/ /include/ /memreserve/ /delete-node/ /delete-property/ /omit-if-no-ref/ compatible reg status interrupts interrupt-parent device_type model #address-cells #size-cells #interrupt-cells ranges dma-ranges pinctrl-names pinctrl-0 clocks clock-names resets reset-names vdd-supply gpio-controller #gpio-cells",
        "Kconfig" => "config menuconfig choice endchoice menu endmenu if endif source mainmenu comment default bool boolean tristate string int hex prompt depends on select imply help def_bool def_tristate visible modules option y n m",
        "SELinux" => "allow auditallow dontaudit neverallow type typeattribute attribute class bool true false type_transition type_change type_member role_transition user role dominance types policycap perms permissive unconfined domain genfscon fs_use_xattr fs_use_task fs_use_trans sid portcon netifcon nodecon mlsconstrain mlsvalidatetrans constrain validatetrans",
        "Blueprint" => "true false cc_library cc_library_shared cc_library_static cc_binary cc_defaults cc_prebuilt_library_shared cc_prebuilt_binary android_app android_app_certificate android_library java_library java_defaults java_binary prebuilt_etc rust_binary rust_library rust_defaults filegroup genrule subdirs optional_subdirs",
        "AIDL" => "package import interface parcelable enum union oneway in out inout const void boolean byte char int long float double String IBinder List Map generic struct types safe_union",
        "Groovy" => "as assert break case catch class const continue def default do else enum extends false finally for goto if implements import in instanceof interface native new null package return super switch this throw throws trait true try var void volatile while plugins android dependencies repositories subprojects allprojects apply task implementation api kapt compileOnly runtimeOnly",
        "Zig" => "addrspace align allowzero and anyerror anytype anyframe asm async await break callconv catch comptime comptime_float comptime_int const continue defer dynlink else enum errdefer error export extern false fn for if inline linksection noalias noinline nosuspend null orelse or packed pub resume return struct suspend switch test threadlocal true try type union unreachable usingnamespace var volatile while yield",
        "Protobuf" => "syntax message enum service rpc returns package import option optional required repeated singular map reserved extensions to max extend true false int32 int64 uint32 uint64 sint32 sint64 fixed32 fixed64 sfixed32 sfixed64 bool string bytes double float oneof",
        "Objective-C" => "abstract alignas auto bool boolean break case catch char class const constexpr continue default delete do double else enum explicit extern false final finally float for friend if import include inline int interface long namespace new null nullptr operator override package private protected public register return short signed sizeof static struct super switch template this throw true try typedef typename union unsigned using virtual void volatile while @interface @implementation @protocol @end @property @synthesize @dynamic @optional @required @class @selector @encode @synchronized @autoreleasepool @try @catch @finally @throw YES NO nil Nil self super id instancetype readonly readwrite assign retain copy nonatomic atomic nullable nonnull null_resettable _Nonnull _Nullable",
        "WAT" => "module func param result local export import memory table global data elem start type select call call_indirect drop block loop if else end br br_if br_table return unreachable nop i32 i64 f32 f64 const load store mut",
        "Scala" => "abstract case catch class def do else extends false final finally for forSome if implicit import lazy match new null object override package private protected return sealed super this throw trait true try type val var while with yield",
        "Elixir" => "after and andalso band bnot bor bsl bsr bxor case catch cond def defmacro defmodule defp defprotocol defimpl do else end false fn for if in nil not or orelse receive rescue true try unless when with",
        "Haskell" => "as case of class data default deriving do forall foreign hiding if then else import in infix infixl infixr instance let mdo module newtype qualified type where True False",
        "Clojure" => "def defn defmacro defmethod defmulti defprotocol defrecord deftype fn let loop recur if when when-not if-not cond case do nil true false ns quote var try catch finally throw",
        "Nim" => "addr and as asm bind block break case cast concept const continue converter defer discard distinct div do elif else end enum except export false finally for from func if import in include interface is isnot iterator let macro method mixin mod nil not notin object of or out proc ptr raise ref return shl shr static template true try tuple type using var when while xor yield",
        "Vimscript" => "let unlet if else elseif endif while endwhile for in endfor function endfunction def enddef return try catch finally endtry command set call echo echomsg echohl execute finish",
        "JSON" => "true false null",
        "SQL" => "SELECT FROM WHERE JOIN LEFT RIGHT INNER OUTER ON AS INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE DROP ALTER INDEX PRIMARY KEY NOT NULL AND OR ORDER BY GROUP HAVING LIMIT OFFSET DISTINCT UNION ALL EXISTS IN IS TRUE FALSE BEGIN END CASE WHEN THEN ELSE",
        "Shell" => "if then else elif fi for while do done case esac in function export local readonly return exit source",
        "TOML" | "YAML" | "INI" => "true false null yes no on off",
        _ => "",
    };
    if matches!(language, "SQL" | "Assembly" | "Batch" | "Dockerfile" | "CMake" | "Kconfig" | "WAT") {
        keywords
            .split_whitespace()
            .any(|w| w.eq_ignore_ascii_case(word))
    } else {
        keywords.split_whitespace().any(|w| w == word)
    }
}

/// Spans use UTF-16 offsets for QTextLayout. Work is bounded by the caller.
pub fn lex(text: &str, language: &str, mut state: State) -> (Vec<(usize, usize, u8)>, State) {
    let mut spans = Vec::new();
    if language == "Plain Text" {
        return (spans, State::default());
    }
    let bytes = text.as_bytes();

    if language == "Diff" {
        if bytes.starts_with(b"+") {
            spans.push((0, text.encode_utf16().count(), 8));
        } else if bytes.starts_with(b"-") {
            spans.push((0, text.encode_utf16().count(), 1));
        } else if bytes.starts_with(b"@@") {
            spans.push((0, text.encode_utf16().count(), 6));
        } else if bytes.starts_with(b"diff ") || bytes.starts_with(b"index ") {
            spans.push((0, text.encode_utf16().count(), 5));
        }
        return (spans, State::default());
    }

    let mut i = 0;
    let mut utf16 = 0;
    let c_family = matches!(
        language,
        "Rust"
            | "JavaScript"
            | "TypeScript"
            | "C"
            | "C++"
            | "C#"
            | "Java"
            | "Kotlin"
            | "Swift"
            | "Go"
            | "CSS"
            | "QML"
            | "JSON"
            | "PHP"
            | "Dart"
            | "Device Tree"
            | "Blueprint"
            | "AIDL"
            | "Groovy"
            | "Zig"
            | "Protobuf"
            | "Objective-C"
            | "Scala"
    );
    let hash_comments = matches!(
        language,
        "Python"
            | "Shell"
            | "TOML"
            | "YAML"
            | "INI"
            | "Ruby"
            | "Perl"
            | "R"
            | "Makefile"
            | "Dockerfile"
            | "CMake"
            | "PowerShell"
            | "GraphQL"
            | "Smali"
            | "Kconfig"
            | "SELinux"
            | "Elixir"
            | "Nim"
    );
    let semicolon_comments = matches!(language, "Assembly" | "INI" | "Clojure");
    let dash_comments = matches!(language, "SQL" | "Lua" | "Haskell");

    while i < bytes.len() {
        let start = i;
        let start16 = utf16;
        let mut kind = 0;

        let is_batch_comment = language == "Batch"
            && (bytes[i..].starts_with(b"::")
                || ((i == 0 || text[..i].trim().is_empty())
                    && (bytes[i..].to_ascii_lowercase().starts_with(b"rem ")
                        || bytes[i..].eq_ignore_ascii_case(b"rem"))));
        let is_asm_comment = language == "Assembly"
            && (bytes[i] == b';' || bytes[i..].starts_with(b"//") || bytes[i] == b'@');
        let is_wat_comment = language == "WAT" && bytes[i..].starts_with(b";;");
        let is_vim_comment = language == "Vimscript"
            && bytes[i] == b'"'
            && (i == 0 || text[..i].chars().last().is_some_and(|c| c.is_whitespace()));

        if state.raw_hashes > 0 {
            kind = 2;
            let hashes = (state.raw_hashes - 1) as usize;
            let closing = format!("\"{}", "#".repeat(hashes));
            if let Some(end) = text[i..].find(&closing) {
                i += end + closing.len();
                state.raw_hashes = 0;
            } else {
                i = bytes.len();
            }
        } else if state.mode == 1 || state.mode == 6 || state.mode == 7 || state.mode == 8 {
            kind = 3;
            let closing = if state.mode == 6 {
                ";)"
            } else if state.mode == 7 {
                "-}"
            } else if state.mode == 8 {
                "]#"
            } else if language == "PowerShell" {
                "#>"
            } else if language == "Lua" {
                "]]"
            } else {
                "*/"
            };
            while i < bytes.len() {
                if language == "Rust" && bytes[i..].starts_with(b"/*") {
                    state.depth = state.depth.saturating_add(1);
                    i += 2;
                } else if bytes[i..].starts_with(closing.as_bytes()) {
                    i += closing.len();
                    state.depth = state.depth.saturating_sub(1);
                    if state.depth == 0 {
                        state.mode = 0;
                        break;
                    }
                } else {
                    i += text[i..].chars().next().unwrap().len_utf8();
                }
            }
        } else if matches!(state.mode, 2..=5) {
            kind = if state.mode == 5 { 3 } else { 2 };
            let closing = match state.mode {
                2 => "'''",
                3 => "\"\"\"",
                4 => {
                    if language == "Lua" {
                        "]]"
                    } else {
                        "`"
                    }
                }
                _ => "-->",
            };
            while i < bytes.len() {
                if bytes[i] == b'\\' && state.mode != 5 {
                    i += 1;
                    if i < bytes.len() {
                        i += text[i..].chars().next().unwrap().len_utf8();
                    }
                } else if text[i..].starts_with(closing) {
                    i += closing.len();
                    state.mode = 0;
                    break;
                } else {
                    i += text[i..].chars().next().unwrap().len_utf8();
                }
            }
        } else if (c_family && bytes[i..].starts_with(b"//"))
            || (hash_comments && bytes[i] == b'#')
            || (semicolon_comments && bytes[i] == b';')
            || is_asm_comment
            || is_batch_comment
            || is_wat_comment
            || is_vim_comment
            || (dash_comments && bytes[i..].starts_with(b"--"))
            || (language == "Markdown" && i == 0 && bytes[i] == b'#')
        {
            kind = if language == "Markdown" { 1 } else { 3 };
            i = bytes.len();
        } else if (c_family || language == "SQL") && bytes[i..].starts_with(b"/*") {
            kind = 3;
            i += 2;
            state.mode = 1;
            state.depth = 1;
        } else if language == "PowerShell" && bytes[i..].starts_with(b"<#") {
            kind = 3;
            i += 2;
            state.mode = 1;
            state.depth = 1;
        } else if language == "Lua" && bytes[i..].starts_with(b"--[[") {
            kind = 3;
            i += 4;
            state.mode = 1;
            state.depth = 1;
        } else if language == "WAT" && bytes[i..].starts_with(b"(;") {
            kind = 3;
            i += 2;
            state.mode = 6;
            state.depth = 1;
        } else if language == "Haskell" && bytes[i..].starts_with(b"{-") {
            kind = 3;
            i += 2;
            state.mode = 7;
            state.depth = 1;
        } else if language == "Nim" && bytes[i..].starts_with(b"#[") {
            kind = 3;
            i += 2;
            state.mode = 8;
            state.depth = 1;
        } else if matches!(language, "HTML" | "XML") && bytes[i..].starts_with(b"<!--") {
            kind = 3;
            i += 4;
            state.mode = 5;
        } else if language == "Rust"
            && bytes[i] == b'r'
            && bytes.get(i + 1).is_some_and(|b| *b == b'#' || *b == b'"')
        {
            let mut next = i + 1;
            while bytes.get(next) == Some(&b'#') {
                next += 1;
            }
            if bytes.get(next) == Some(&b'"') {
                kind = 2;
                state.raw_hashes = ((next - i - 1) as u16).saturating_add(1);
                i = next + 1;
            } else {
                i += 1;
            }
        } else if (matches!(language, "Python" | "Swift" | "Kotlin" | "Scala" | "Groovy")
            && bytes[i..].starts_with(b"\"\"\""))
            || (language == "Python" && bytes[i..].starts_with(b"'''"))
        {
            kind = 2;
            state.mode = if bytes[i] == b'\'' { 2 } else { 3 };
            i += 3;
        } else if language == "Lua" && bytes[i..].starts_with(b"[[") {
            kind = 2;
            state.mode = 4;
            i += 2;
        } else if bytes[i] == b'`'
            && matches!(
                language,
                "JavaScript" | "TypeScript" | "QML" | "Go" | "Markdown" | "Scala"
            )
        {
            kind = 2;
            state.mode = 4;
            i += 1;
        } else if matches!(bytes[i], b'"' | b'\'') {
            let is_lifetime = language == "Rust" && bytes[i] == b'\'' && {
                let rest = &text[i + 1..];
                let first = rest.chars().next();
                first.is_some_and(|c| c.is_alphabetic())
                    && !rest[first.unwrap().len_utf8()..].starts_with('\'')
            };
            if is_lifetime {
                i += 1;
            } else {
                kind = 2;
                let quote = bytes[i];
                i += 1;
                while i < bytes.len() {
                    if bytes[i] == b'\\' {
                        i += 1;
                        if i < bytes.len() {
                            i += text[i..].chars().next().unwrap().len_utf8();
                        }
                    } else if bytes[i] == quote {
                        i += 1;
                        break;
                    } else {
                        i += text[i..].chars().next().unwrap().len_utf8();
                    }
                }
            }
        } else if bytes[i].is_ascii_digit()
            || (language == "Assembly"
                && bytes[i] == b'$'
                && bytes.get(i + 1).is_some_and(|b| {
                    b.is_ascii_digit()
                        || (b'a'..=b'f').contains(b)
                        || (b'A'..=b'F').contains(b)
                }))
        {
            kind = 4;
            i += 1;
            while i < bytes.len()
                && (bytes[i].is_ascii_alphanumeric() || matches!(bytes[i], b'.' | b'_'))
            {
                i += 1;
            }
        } else if matches!(language, "HTML" | "XML") && bytes[i] == b'<' {
            kind = 8;
            i += 1;
            while i < bytes.len()
                && (bytes[i].is_ascii_alphanumeric() || matches!(bytes[i], b'/' | b'-' | b':'))
            {
                i += 1;
            }
        } else {
            let ch = text[i..].chars().next().unwrap();
            let is_ident_start = ch.is_alphabetic()
                || ch == '_'
                || ch == '$'
                || (matches!(language, "Smali" | "Assembly") && ch == '.')
                || (language == "Objective-C" && ch == '@')
                || (language == "Device Tree" && (ch == '/' || ch == '&'))
                || (language == "INI" && ch == '[');

            if is_ident_start {
                i += ch.len_utf8();
                if language == "INI" && ch == '[' {
                    while i < bytes.len() {
                        let next = text[i..].chars().next().unwrap();
                        i += next.len_utf8();
                        if next == ']' {
                            break;
                        }
                    }
                    kind = 5;
                } else {
                    let smali = language == "Smali";
                    let clojure = language == "Clojure";
                    let dts = language == "Device Tree";
                    while i < bytes.len() {
                        let next = text[i..].chars().next().unwrap();
                        if next.is_alphanumeric()
                            || next == '_'
                            || next == '$'
                            || (smali && (next == '-' || next == '/'))
                            || (clojure && (next == '-' || next == '?' || next == '!' || next == '*' || next == '+'))
                            || (dts && (next == '-' || next == '/' || next == '#'))
                        {
                            i += next.len_utf8();
                        } else {
                            break;
                        }
                    }
                    let word = &text[start..i];
                    if keyword(language, word) {
                        kind = 1;
                    } else if (language == "Smali" && word.starts_with('.'))
                        || (language == "Assembly" && word.starts_with('.'))
                    {
                        kind = 1;
                    } else if smali
                        && (word.starts_with('v') || word.starts_with('p'))
                        && word.len() > 1
                        && word[1..].chars().all(|c| c.is_ascii_digit())
                    {
                        kind = 8;
                    } else if language == "INI" && text[i..].trim_start().starts_with('=') {
                        kind = 8;
                    } else if ch.is_uppercase() {
                        kind = 5;
                    } else if text[i..].trim_start().starts_with('(') {
                        kind = 6;
                    }
                }
            } else {
                i += ch.len_utf8();
                if "+-=*/<>!&|?:%@".contains(ch) {
                    kind = 7;
                }
            }
        }
        utf16 += text[start..i].encode_utf16().count();
        if kind != 0 {
            spans.push((start16, utf16 - start16, kind));
        }
    }
    (spans, state)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn multiline_and_nested_comments() {
        let (_, state) = lex("/* outer /* nested", "Rust", State::default());
        let (spans, state) = lex("*/ still */ let x = 2;", "Rust", state);
        assert_eq!(state, State::default());
        assert!(spans.iter().any(|s| s.2 == 1));
    }

    #[test]
    fn python_multiline_strings() {
        let (_, state) = lex("x = '''hello", "Python", State::default());
        let (spans, state) = lex("world''' + 42", "Python", state);
        assert_eq!(state, State::default());
        assert!(spans.iter().any(|s| s.2 == 4));
    }

    #[test]
    fn swift_and_kotlin_multiline_strings() {
        let (_, state) = lex("let s = \"\"\"hello", "Swift", State::default());
        let (spans, state) = lex("world\"\"\" + 1", "Swift", state);
        assert_eq!(state, State::default());
        assert!(spans.iter().any(|s| s.2 == 4));
    }

    #[test]
    fn utf16_not_utf8_spans() {
        let (spans, _) = lex("😀 let crab = 2;", "Rust", State::default());
        assert!(spans.contains(&(3, 3, 1)));
    }

    #[test]
    fn rust_raw_and_lifetimes() {
        let (_, state) = lex("let s = r##\"hello", "Rust", State::default());
        let (spans, state) = lex("world\"##;", "Rust", state);
        assert_eq!(state, State::default());
        assert!(spans.iter().any(|s| s.2 == 2));
        let (spans, _) = lex("fn borrow<'a>(x: &'a str)", "Rust", State::default());
        assert!(!spans.iter().any(|s| s.2 == 2));
    }

    #[test]
    fn assembly_and_smali_lexing() {
        let (spans, _) = lex("mov eax, 1 ; set eax", "Assembly", State::default());
        assert!(spans.iter().any(|s| s.2 == 1)); // mov keyword
        assert!(spans.iter().any(|s| s.2 == 3)); // ; comment

        let (spans_smali, _) = lex(".method public onCreate()V\ninvoke-virtual {v0}, Landroid/app/Activity;->onCreate()V", "Smali", State::default());
        assert!(spans_smali.iter().any(|s| s.2 == 1)); // .method or invoke-virtual keyword
    }

    #[test]
    fn ini_and_diff_lexing() {
        let (spans_diff, _) = lex("+added line", "Diff", State::default());
        assert_eq!(spans_diff[0].2, 8);
        let (spans_diff_del, _) = lex("-removed line", "Diff", State::default());
        assert_eq!(spans_diff_del[0].2, 1);

        let (spans_ini, _) = lex("[ro.build.display]\nro.build.id=AP1A", "INI", State::default());
        assert!(spans_ini.iter().any(|s| s.2 == 5)); // [section]
    }

    #[test]
    fn android_kernel_and_system_languages_lexing() {
        let (spans_dts, _) = lex("/dts-v1/;\n/ { model = \"Test Phone\"; };", "Device Tree", State::default());
        assert!(spans_dts.iter().any(|s| s.2 == 1)); // /dts-v1/ or model keyword

        let (spans_kconfig, _) = lex("config ANDROID_BINDER_IPC\n    bool \"Android Binder IPC\"\n    default y", "Kconfig", State::default());
        assert!(spans_kconfig.iter().any(|s| s.2 == 1)); // config or bool keyword

        let (spans_selinux, _) = lex("allow system_server app_data_file:file rw_file_perms;", "SELinux", State::default());
        assert!(spans_selinux.iter().any(|s| s.2 == 1)); // allow keyword

        let (spans_bp, _) = lex("cc_binary {\n    name: \"kitchen_helper\",\n    srcs: [\"main.cpp\"],\n}", "Blueprint", State::default());
        assert!(spans_bp.iter().any(|s| s.2 == 1)); // cc_binary keyword

        let (spans_zig, _) = lex("pub fn main() !void {\n    const x: u32 = 123;\n}", "Zig", State::default());
        assert!(spans_zig.iter().any(|s| s.2 == 1)); // pub or fn or const
    }
}
