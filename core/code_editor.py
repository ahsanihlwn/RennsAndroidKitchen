# SPDX-License-Identifier: MPL-2.0
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor
from PySide6.QtCore import QRegularExpression
import re

print("[CORE STAGE] CODE EDIT RUNNING")

class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document):


        self._hl_enabled = False
        super().__init__(document)

        self.rules = []

        # WARNA DEFINISI
        self.fmt_keyword     = self.make_format("#61afef")   # on, service, class
        self.fmt_property    = self.make_format("#c792ea")
        self.fmt_service_opt = self.make_format("#e5c07b")
        self.fmt_path        = self.make_format("#d19a66")
        self.fmt_string      = self.make_format("#98c379")
        self.fmt_bool        = self.make_format("#56b6c2")
        self.fmt_octal       = self.make_format("#e06c75")
        self.fmt_chain       = self.make_format("#e5c07b")
        self.fmt_comment     = self.make_format("#9599a1")
        self.fmt_tokenA      = self.make_format("#d3869b")   # arg1
        self.fmt_tokenB      = self.make_format("#b8bb26")   # arg2
        self.fmt_tokenC      = self.make_format("#fabd2f")   # arg3
        self.fmt_tokenD      = self.make_format("#83a598")   # arg4
        self.fmt_var         = self.make_format("#4fc1e9") # $VAR env variables

        # konsisten token map
        self.token_map = {}


        # string
        self.rules.append((QRegularExpression(r"\".*?\""), self.fmt_string))

        # boolean
        self.rules.append((QRegularExpression(r"\b(true|false)\b"), self.fmt_bool))

        # permissions
        self.rules.append((QRegularExpression(r"\b0[0-7]{3,4}\b"), self.fmt_octal))
        self.rules.append((QRegularExpression(r"\bchmod\s+0[0-7]{3,4}\b"), self.fmt_octal))

        # dot chain a.b.c
        self.rules.append((QRegularExpression(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+"), self.fmt_chain))

        # { .. } and [ .. ]
        brace_color   = self.make_format("#e57373")
        bracket_color = self.make_format("#80cbc4")
        self.rules.append((QRegularExpression(r"\{[^}]*\}"), brace_color))
        self.rules.append((QRegularExpression(r"\[[^\]]*\]"), bracket_color))

        # property:setprop
        self.rules.append((QRegularExpression(r"\bsetprop\s+[A-Za-z0-9_.]+\s+[^ \n]+"), self.fmt_property))
        self.rules.append((QRegularExpression(r"property:[A-Za-z0-9_.=]+"), self.fmt_property))


    def make_format(self, color_hex):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color_hex))
        return fmt


    def color_for_token(self, token):

        if token in self.token_map:
            return self.token_map[token]

        # assign warna berdasarkan urutan
        choice = len(self.token_map) % 4
        if choice == 0:
            self.token_map[token] = self.fmt_tokenA
        elif choice == 1:
            self.token_map[token] = self.fmt_tokenB
        elif choice == 2:
            self.token_map[token] = self.fmt_tokenC
        else:
            self.token_map[token] = self.fmt_tokenD

        return self.token_map[token]


    def highlightBlock(self, text):
            if not self._hl_enabled:
                return   # noop selama loading — Qt hajar tapi ga ada kerja

            spans = []  # (start, length, format, priority)

            # PASS 1: special use
            spans += self.apply_special_use(text)

            # PASS 2: power use
            spans += self.apply_power_use(text)

            # PASS 3: rc structural
            spans += self.apply_rc_structure(text)

            # APPLY SPANS: highest priority wins
            self.apply_spans(spans)

    def apply_power_use(self, text):
            spans = []

            # @text
            it = QRegularExpression(r'@[A-Za-z0-9_]+').globalMatch(text)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_bool, 80))

            # =true / =false
            it = QRegularExpression(r'=(true|false)').globalMatch(text)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_bool, 80))

            # -A / -a
            it = QRegularExpression(r'-[A-Za-z]').globalMatch(text)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_service_opt, 80))

            return spans


    def apply_special_use(self, text):
            spans = []


            # If previous line was inside block comment
            if self.previousBlockState() == 1:
                end_idx = text.find(">")
                if end_idx == -1:
                    # whole line is comment
                    spans.append((0, len(text), self.fmt_comment, 200))
                    self.setCurrentBlockState(1)
                    return spans
                else:
                    # comment ends here
                    spans.append((0, end_idx + 1, self.fmt_comment, 200))
                    # continue parsing rest
                    text_after = text[end_idx+1:]
                    self.setCurrentBlockState(0)
            else:
                text_after = text


            start_idx = text_after.find("<!")
            if start_idx != -1:
                end_idx = text_after.find(">", start_idx + 2)

                if end_idx == -1:
                    # open multiline comment, no closing on this line
                    spans.append((start_idx, len(text_after) - start_idx, self.fmt_comment, 200))
                    self.setCurrentBlockState(1)
                    return spans
                else:
                    # open + close on same line
                    spans.append((start_idx, end_idx - start_idx + 1, self.fmt_comment, 200))
                    # continue with anything after '>'
                    remaining = text_after[end_idx+1:]
                    # parse remaining normally
                    text_after = remaining
                    self.setCurrentBlockState(0)


            stripped = text_after.lstrip()
            if stripped.startswith("#"):
                # highlight from first non-space until end
                start = text_after.index("#")
                spans.append((start, len(text_after) - start, self.fmt_comment, 200))
                return spans


            # colon-chain + optional =(....)
            colon_pattern = (
                r'[A-Za-z_]\w*'
                r'(?:\.[A-Za-z_]\w*)*'
                r':' 
                r'[A-Za-z_]\w*'
                r'(?:\.[A-Za-z_]\w*)*'
                r'(?:=\([^)]*\))?'
            )
            it = QRegularExpression(colon_pattern).globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append((m.capturedStart(), m.capturedLength(), self.fmt_property, 150))

            # ANYTHING with 2+ dashes: lah-lah-lah-ampeujung
            it = QRegularExpression(r'\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}\b').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append((m.capturedStart(), m.capturedLength(), self.fmt_keyword, 150))

            # on-something (special use)
            it = QRegularExpression(r'\bon-[A-Za-z0-9\-]+').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append((m.capturedStart(), m.capturedLength(), self.fmt_keyword, 150))


            # "string"
            it = QRegularExpression(r'"[^"]*"').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_string, 100))

            # <xml>
            it = QRegularExpression(r'<[^>]+>').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_keyword, 100))

            # double dash --text
            it = QRegularExpression(r'--[A-Za-z0-9_-]+').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_property, 100))

            # ${VAR} and $VAR — environment variables in RC/init
            it = QRegularExpression(r'\$\{[A-Za-z0-9_]+\}|\$[A-Za-z0-9_]+').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_bool, 100))

            # path
            it = QRegularExpression(r'[/\\][A-Za-z0-9._/\-]+').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_path, 100))

            # underscore chain
            it = QRegularExpression(r'\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_chain, 100))

            # assignment
            it = QRegularExpression(r'\b[A-Za-z_]\w*(?==)').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_property, 100))

            # text:text simple
            it = QRegularExpression(r'\b[A-Za-z_]\w*:[A-Za-z0-9_]+\b').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                spans.append(self.span(m, self.fmt_property, 100))

            # {content}
            it = QRegularExpression(r'\{[^}]*\}').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                s = m.capturedStart()
                l = m.capturedLength()
                if l > 2:
                    spans.append((s, 1, self.fmt_octal, 100))
                    spans.append((s+1, l-2, self.fmt_string, 100))
                    spans.append((s+l-1, 1, self.fmt_octal, 100))
                else:
                    spans.append(self.span(m, self.fmt_octal, 100))

            # [content]
            it = QRegularExpression(r'\[[^\]]*\]').globalMatch(text_after)
            while it.hasNext():
                m = it.next()
                s = m.capturedStart()
                l = m.capturedLength()
                if l > 2:
                    spans.append((s, 1, self.fmt_octal, 100))
                    spans.append((s+1, l-2, self.fmt_string, 100))
                    spans.append((s+l-1, 1, self.fmt_octal, 100))
                else:
                    spans.append(self.span(m, self.fmt_octal, 100))

            return spans

    def apply_rc_structure(self, text):
        spans = []

        stripped = text.lstrip()
        indent = len(text) - len(stripped)
        parts = stripped.split()

        if not parts:
            return spans

        head = parts[0]

        # service line
        if head == "service" and len(parts) >= 2:
            st = text.index("service")
            spans.append((st, 7, self.fmt_keyword, 20))

            args = parts[1:]
            cursor = st + 7

            for i, arg in enumerate(args):
                pos = text.index(arg, cursor)

                fmt = [self.fmt_tokenA, self.fmt_tokenB, self.fmt_tokenC, self.fmt_tokenD][i % 4]
                spans.append((pos, len(arg), fmt, 20))

                cursor = pos + len(arg)

            return spans

        # indent block (user, group, oneshot, disabled)
        if indent >= 4:
            tokens = stripped.split()
            if tokens:
                head = tokens[0]
                spans.append((text.index(head), len(head), self.fmt_keyword, 20))

                args = tokens[1:]
                cursor = text.index(args[0]) if args else -1
                for arg in args:
                    pos = text.index(arg, cursor)
                    spans.append((pos, len(arg), self.color_for_token(arg), 20))
                    cursor = pos + len(arg)

            return spans

        return spans

    # Standalone compute interface (untuk _apply_highlight_once)
    def setCurrentBlockState_sim(self, state):
        self._sim_block_state      = state
        self._sim_current_state    = 0

    def get_block_state(self):
        return getattr(self, "_sim_current_state", 0)

    def previousBlockState(self):
        return getattr(self, "_sim_block_state", -1)

    def setCurrentBlockState(self, state):
        self._sim_current_state = state
        # juga panggil parent kalau ada document
        if self.document() is not None:
            try:
                super().setCurrentBlockState(state)
            except Exception:
                pass

    def compute_spans(self, text):

        spans = []
        spans += self.apply_special_use(text)
        spans += self.apply_power_use(text)
        spans += self.apply_rc_structure(text)
        return spans

    def span(self, m, fmt, prio):
        return (m.capturedStart(), m.capturedLength(), fmt, prio)

    def apply_spans(self, spans):
        # sort priority descending
        spans.sort(key=lambda x: -x[3])

        used = [False] * self.currentBlock().length()

        for s, l, fmt, pr in spans:
            # skip if already filled by higher-priority
            if any(used[s:s+l]):
                continue

            self.setFormat(s, l, fmt)

            for i in range(s, s+l):
                used[i] = True

class XmlHighlighter(QSyntaxHighlighter):
    def __init__(self, document=None):
        self._hl_enabled = False
        super().__init__(document)

        self.fmt_tag        = self._fmt("#e06c75")   # <tag />
        self.fmt_attr       = self._fmt("#d19a66")   # attribute name
        self.fmt_value      = self._fmt("#98c379")   # "value"
        self.fmt_comment    = self._fmt("#5c6370")   # <!-- -->
        self.fmt_cdata      = self._fmt("#56b6c2")   # <![CDATA[]]>
        self.fmt_special    = self._fmt("#c678dd")   # &amp; &lt; etc
        self.fmt_decl       = self._fmt("#61afef")   # <?xml ?> <!DOCTYPE>
        self.fmt_bracket    = self._fmt("#abb2bf")   # < > / =

        # multiline comment state
        self.IN_COMMENT = 1
        self.IN_CDATA   = 2

    def _fmt(self, color):
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        return f

    def highlightBlock(self, text):
        if not self._hl_enabled:
            return
        if not getattr(self, "_xml_mode", False):
            return

        prev = self.previousBlockState()

        # lanjut dari multiline comment
        if prev == self.IN_COMMENT:
            end = text.find("-->")
            if end == -1:
                self.setFormat(0, len(text), self.fmt_comment)
                self.setCurrentBlockState(self.IN_COMMENT)
                return
            self.setFormat(0, end + 3, self.fmt_comment)
            self.setCurrentBlockState(0)
            self._highlight_xml(text, end + 3)
            return

        # lanjut dari multiline CDATA
        if prev == self.IN_CDATA:
            end = text.find("]]>")
            if end == -1:
                self.setFormat(0, len(text), self.fmt_cdata)
                self.setCurrentBlockState(self.IN_CDATA)
                return
            self.setFormat(0, end + 3, self.fmt_cdata)
            self.setCurrentBlockState(0)
            self._highlight_xml(text, end + 3)
            return

        self.setCurrentBlockState(0)
        self._highlight_xml(text, 0)

    def _highlight_xml(self, text, start):
        i = start
        n = len(text)

        while i < n:
            # komentar <!
            if text[i:i+4] == "<!--":
                end = text.find("-->", i + 4)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_comment)
                    self.setCurrentBlockState(self.IN_COMMENT)
                    return
                self.setFormat(i, end + 3 - i, self.fmt_comment)
                i = end + 3
                continue

            # CDATA <![CDATA[
            if text[i:i+9] == "<![CDATA[":
                end = text.find("]]>", i + 9)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_cdata)
                    self.setCurrentBlockState(self.IN_CDATA)
                    return
                self.setFormat(i, end + 3 - i, self.fmt_cdata)
                i = end + 3
                continue

            # deklarasi <?xml atau <!DOCTYPE
            if text[i:i+2] in ("<?", "<!"):
                end = text.find(">", i + 2)
                length = (end + 1 - i) if end != -1 else (n - i)
                self.setFormat(i, length, self.fmt_decl)
                i = i + length
                continue

            # tag pembuka/penutup
            if text[i] == "<":
                # cari penutup >
                end = text.find(">", i + 1)
                if end == -1:
                    end = n - 1

                tag_str = text[i:end+1]

                # bracket < >
                self.setFormat(i, 1, self.fmt_bracket)
                self.setFormat(end, 1, self.fmt_bracket)

                # slash / di </tag> atau <tag/>
                j = i + 1
                if j < end and tag_str[1] == "/":
                    self.setFormat(j, 1, self.fmt_bracket)
                    j += 1
                elif end > i and tag_str[-2] == "/":
                    self.setFormat(end - 1, 1, self.fmt_bracket)

                # nama tag — sampai spasi atau > atau /
                tag_start = j
                while j < end and text[j] not in (" ", "\t", "/", ">"):
                    j += 1
                if j > tag_start:
                    self.setFormat(tag_start, j - tag_start, self.fmt_tag)

                # attributes di dalam tag
                self._highlight_attrs(text, j, end)

                i = end + 1
                continue

            # entity &amp; &lt; etc
            if text[i] == "&":
                end = text.find(";", i + 1)
                if end != -1 and end - i < 12:
                    self.setFormat(i, end + 1 - i, self.fmt_special)
                    i = end + 1
                    continue

            i += 1

    def _highlight_attrs(self, text, start, end):

        i = start
        # attr name regex: word chars sampai =
        attr_re  = QRegularExpression(r'\b[\w:-]+(?=\s*=)')
        value_re = QRegularExpression(r'"[^"]*"|\'[^\']*\'')

        it = attr_re.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            if m.capturedStart() >= end:
                break
            self.setFormat(m.capturedStart(), m.capturedLength(), self.fmt_attr)

        it = value_re.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            if m.capturedStart() >= end:
                break
            self.setFormat(m.capturedStart(), m.capturedLength(), self.fmt_value)


class _BaseHL(QSyntaxHighlighter):
    IN_COMMENT  = 1
    IN_STRING   = 2
    IN_MLSTRING = 3

    def __init__(self, document=None):
        self._hl_enabled = False
        super().__init__(document)

    def _fmt(self, color, bold=False, italic=False):
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:   f.setFontWeight(700)
        if italic: f.setFontItalic(True)
        return f

    def highlightBlock(self, text):
        if not self._hl_enabled:
            return
        self._highlight(text)

    def _highlight(self, text):
        pass


class JsonHighlighter(_BaseHL):
    def __init__(self, document=None):
        super().__init__(document)
        self._json_mode  = False
        self.fmt_key     = self._fmt("#61afef")
        self.fmt_str     = self._fmt("#98c379")
        self.fmt_num     = self._fmt("#d19a66")
        self.fmt_kw      = self._fmt("#56b6c2")
        self.fmt_bracket = self._fmt("#e5c07b")
        self.fmt_colon   = self._fmt("#abb2bf")
        self.fmt_comment = self._fmt("#5c6370", italic=True)

    def _highlight(self, text):
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)
        if prev == self.IN_COMMENT:
            end = text.find("*/")
            if end == -1:
                self.setFormat(0, len(text), self.fmt_comment)
                self.setCurrentBlockState(self.IN_COMMENT)
                return
            self.setFormat(0, end + 2, self.fmt_comment)
            self._scan(text, end + 2)
            return
        self._scan(text, 0)

    def _scan(self, text, start):
        i = start
        n = len(text)
        while i < n:
            if text[i:i+2] == "//":
                self.setFormat(i, n - i, self.fmt_comment)
                return
            if text[i:i+2] == "/*":
                end = text.find("*/", i + 2)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_comment)
                    self.setCurrentBlockState(self.IN_COMMENT)
                    return
                self.setFormat(i, end + 2 - i, self.fmt_comment)
                i = end + 2
                continue
            if text[i] == '"':
                j = i + 1
                while j < n:
                    if text[j] == '\\': j += 2; continue
                    if text[j] == '"': break
                    j += 1
                length = j + 1 - i
                after = text[j+1:].lstrip() if j + 1 < n else ""
                self.setFormat(i, length, self.fmt_key if after.startswith(":") else self.fmt_str)
                i = j + 1
                continue
            if text[i].isdigit() or (text[i] == '-' and i+1 < n and text[i+1].isdigit()):
                j = i + 1
                while j < n and text[j] in "0123456789.eE+-":
                    j += 1
                self.setFormat(i, j - i, self.fmt_num)
                i = j
                continue
            for kw in ("true", "false", "null"):
                kl = len(kw)
                if text[i:i+kl] == kw:
                    nxt = text[i+kl:i+kl+1]
                    if not nxt or not (nxt.isalnum() or nxt == '_'):
                        self.setFormat(i, kl, self.fmt_kw)
                        i += kl
                        break
            else:
                if text[i] in "{}[]":
                    self.setFormat(i, 1, self.fmt_bracket)
                elif text[i] == ':':
                    self.setFormat(i, 1, self.fmt_colon)
                i += 1


class CppHighlighter(_BaseHL):
    _KW_C = {
        "auto","break","case","char","const","continue","default","do",
        "double","else","enum","extern","float","for","goto","if","inline",
        "int","long","register","restrict","return","short","signed","sizeof",
        "static","struct","switch","typedef","union","unsigned","void",
        "volatile","while","_Bool","_Complex","_Imaginary",
    }
    _KW_CPP = {
        "alignas","alignof","and","and_eq","asm","bitand","bitor","bool",
        "catch","class","compl","concept","consteval","constexpr","constinit",
        "const_cast","co_await","co_return","co_yield","decltype","delete",
        "dynamic_cast","explicit","export","false","friend","mutable",
        "namespace","new","noexcept","not","not_eq","nullptr","operator","or",
        "or_eq","private","protected","public","reinterpret_cast","requires",
        "static_assert","static_cast","template","this","thread_local","throw",
        "true","try","typeid","typename","using","virtual","wchar_t","xor","xor_eq",
    }

    def __init__(self, document=None):
        super().__init__(document)
        self._cpp_mode   = False
        self._all_kw     = self._KW_C | self._KW_CPP
        self.fmt_kw      = self._fmt("#c678dd", bold=True)
        self.fmt_type    = self._fmt("#e5c07b")
        self.fmt_str     = self._fmt("#98c379")
        self.fmt_num     = self._fmt("#d19a66")
        self.fmt_comment = self._fmt("#5c6370", italic=True)
        self.fmt_preproc = self._fmt("#e5c07b")
        self.fmt_func    = self._fmt("#61afef")
        self.fmt_macro   = self._fmt("#e5c07b")
        self.fmt_var     = self._fmt("#4fc1e9")   # local variables

        self._kw_re    = QRegularExpression(r'\b(' + '|'.join(sorted(self._all_kw)) + r')\b')
        self._num_re   = QRegularExpression(r'\b(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+\.?\d*([eE][+-]?\d+)?[uUlLfF]*)\b')
        self._func_re  = QRegularExpression(r'\b([a-zA-Z_]\w*)\s*(?=\()')
        self._macro_re = QRegularExpression(r'\b[A-Z][A-Z0-9_]{2,}\b')
        self._str_re   = QRegularExpression(r'"([^"\\]|\\.)*"')
        self._chr_re   = QRegularExpression(r"'([^'\\]|\\.)*'")
        self._pre_re   = QRegularExpression(r'^\s*#\s*\w+')
        self._var_re   = QRegularExpression(r'(?<=\bint |\blong |\bfloat |\bdouble |\bchar |\bbool |\bauto |\bvoid\* |\* )([a-zA-Z_]\w*)(?=\s*[=;,)\[])')

    def _highlight(self, text):
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)
        start = 0
        if prev == self.IN_COMMENT:
            end = text.find("*/")
            if end == -1:
                self.setFormat(0, len(text), self.fmt_comment)
                self.setCurrentBlockState(self.IN_COMMENT)
                return
            self.setFormat(0, end + 2, self.fmt_comment)
            start = end + 2

        self._apply_re(text, self._var_re,    self.fmt_var,     start, group=1)  # var names after type decl
        self._apply_re(text, self._kw_re,    self.fmt_kw,      start)
        self._apply_re(text, self._macro_re,  self.fmt_macro,   start)
        self._apply_re(text, self._num_re,    self.fmt_num,     start)
        self._apply_re(text, self._str_re,    self.fmt_str,     start)
        self._apply_re(text, self._chr_re,    self.fmt_str,     start)
        self._apply_re(text, self._func_re,   self.fmt_func,    start, group=1)
        self._apply_re(text, self._pre_re,    self.fmt_preproc, start)

        i = start
        n = len(text)
        while i < n:
            if text[i:i+2] == "/*":
                end = text.find("*/", i + 2)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_comment)
                    self.setCurrentBlockState(self.IN_COMMENT)
                    return
                self.setFormat(i, end + 2 - i, self.fmt_comment)
                i = end + 2
            elif text[i:i+2] == "//":
                self.setFormat(i, n - i, self.fmt_comment)
                return
            else:
                i += 1

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class HtmlHighlighter(_BaseHL):
    def __init__(self, document=None):
        super().__init__(document)
        self._html_mode  = False
        self.fmt_tag     = self._fmt("#e06c75")
        self.fmt_attr    = self._fmt("#d19a66")
        self.fmt_val     = self._fmt("#98c379")
        self.fmt_comment = self._fmt("#5c6370", italic=True)
        self.fmt_doctype = self._fmt("#61afef")
        self.fmt_entity  = self._fmt("#c678dd")
        self.fmt_bracket = self._fmt("#abb2bf")

        self._attr_re   = QRegularExpression(r'\b[\w:-]+(?=\s*=)')
        self._val_re    = QRegularExpression(r'"[^"]*"|\'[^\']*\'')
        self._entity_re = QRegularExpression(r'&[a-zA-Z#0-9]+;')

    def _highlight(self, text):
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)
        if prev == self.IN_COMMENT:
            end = text.find("-->")
            if end == -1:
                self.setFormat(0, len(text), self.fmt_comment)
                self.setCurrentBlockState(self.IN_COMMENT)
                return
            self.setFormat(0, end + 3, self.fmt_comment)
            self._scan_html(text, end + 3)
            return
        self._scan_html(text, 0)

    def _scan_html(self, text, start):
        i = start
        n = len(text)
        while i < n:
            if text[i:i+4] == "<!--":
                end = text.find("-->", i + 4)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_comment)
                    self.setCurrentBlockState(self.IN_COMMENT)
                    return
                self.setFormat(i, end + 3 - i, self.fmt_comment)
                i = end + 3
                continue
            if text[i:i+9].upper() == "<!DOCTYPE":
                end = text.find(">", i)
                l = (end + 1 - i) if end != -1 else (n - i)
                self.setFormat(i, l, self.fmt_doctype)
                i += l
                continue
            if text[i] == "<":
                end = text.find(">", i + 1)
                if end == -1: end = n - 1
                tag_text = text[i:end+1]
                self.setFormat(i, 1, self.fmt_bracket)
                self.setFormat(end, 1, self.fmt_bracket)
                j = i + 1
                if j <= end and tag_text[1:2] == "/":
                    self.setFormat(j, 1, self.fmt_bracket)
                    j += 1
                k = j
                while k <= end and text[k] not in (" ", "\t", "/", ">", "\n"):
                    k += 1
                if k > j:
                    self.setFormat(j, k - j, self.fmt_tag)
                if end > i and tag_text[-2:-1] == "/":
                    self.setFormat(end - 1, 1, self.fmt_bracket)
                it = self._attr_re.globalMatch(text, k)
                while it.hasNext():
                    m = it.next()
                    if m.capturedStart() > end: break
                    self.setFormat(m.capturedStart(), m.capturedLength(), self.fmt_attr)
                it = self._val_re.globalMatch(text, k)
                while it.hasNext():
                    m = it.next()
                    if m.capturedStart() > end: break
                    self.setFormat(m.capturedStart(), m.capturedLength(), self.fmt_val)
                i = end + 1
                continue
            if text[i] == "&":
                m = self._entity_re.match(text, i)
                if m.hasMatch() and m.capturedStart() == i:
                    self.setFormat(i, m.capturedLength(), self.fmt_entity)
                    i += m.capturedLength()
                    continue
            i += 1


class PythonHighlighter(_BaseHL):
    _KW = {
        "False","None","True","and","as","assert","async","await",
        "break","class","continue","def","del","elif","else","except",
        "finally","for","from","global","if","import","in","is","lambda",
        "nonlocal","not","or","pass","raise","return","try","while","with","yield",
    }
    _BUILTIN = {
        "abs","all","any","bin","bool","breakpoint","bytearray","bytes",
        "callable","chr","classmethod","compile","complex","delattr","dict",
        "dir","divmod","enumerate","eval","exec","filter","float","format",
        "frozenset","getattr","globals","hasattr","hash","help","hex","id",
        "input","int","isinstance","issubclass","iter","len","list","locals",
        "map","max","memoryview","min","next","object","oct","open","ord",
        "pow","print","property","range","repr","reversed","round","set",
        "setattr","slice","sorted","staticmethod","str","sum","super","tuple",
        "type","vars","zip","__import__",
    }

    TRIPLE_DQ = 4
    TRIPLE_SQ = 5

    def __init__(self, document=None):
        super().__init__(document)
        self._py_mode    = False
        self.fmt_kw      = self._fmt("#c678dd", bold=True)
        self.fmt_builtin = self._fmt("#56b6c2")
        self.fmt_str     = self._fmt("#98c379")
        self.fmt_str3    = self._fmt("#98c379", italic=True)
        self.fmt_num     = self._fmt("#d19a66")
        self.fmt_comment = self._fmt("#5c6370", italic=True)
        self.fmt_func    = self._fmt("#61afef")
        self.fmt_class   = self._fmt("#e5c07b", bold=True)
        self.fmt_deco    = self._fmt("#c792ea")
        self.fmt_self    = self._fmt("#e06c75")
        self.fmt_var     = self._fmt("#4fc1e9")  # local variables

        self._kw_re      = QRegularExpression(r'\b(' + '|'.join(sorted(self._KW)) + r')\b')
        self._builtin_re = QRegularExpression(r'\b(' + '|'.join(sorted(self._BUILTIN)) + r')\b')
        self._num_re     = QRegularExpression(r'\b(0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|\d+\.?\d*([eE][+-]?\d+)?[jJ]?)\b')
        self._func_re    = QRegularExpression(r'(?<=def )\s*([a-zA-Z_]\w*)')
        self._class_re   = QRegularExpression(r'(?<=class )\s*([a-zA-Z_]\w*)')
        self._deco_re    = QRegularExpression(r'@[a-zA-Z_][\w.]*')
        self._self_re    = QRegularExpression(r'\b(self|cls)\b')
        self._var_re     = QRegularExpression(r'\b([a-z_][a-zA-Z0-9_]*)\s*(?==(?!=))|(?<=\bfor )[a-z_]\w*|(?<=\bas )[a-z_]\w*')

    def _highlight(self, text):
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)

        # lanjut dari triple-quote multiline
        if prev in (self.TRIPLE_DQ, self.TRIPLE_SQ):
            delim = '"""' if prev == self.TRIPLE_DQ else "'''"
            end = text.find(delim)
            if end == -1:
                self.setFormat(0, len(text), self.fmt_str3)
                self.setCurrentBlockState(prev)
                return
            self.setFormat(0, end + 3, self.fmt_str3)
            self._scan_py(text, end + 3)
            return

        self._scan_py(text, 0)

    def _scan_py(self, text, start):
        n = len(text)

        # Pass 1: token (keyword, builtin, dll)
        self._apply_re(text, self._var_re,      self.fmt_var,      start)  # var: assignment lhs & for/as targets
        self._apply_re(text, self._kw_re,      self.fmt_kw,      start)
        self._apply_re(text, self._builtin_re,  self.fmt_builtin,  start)
        self._apply_re(text, self._num_re,      self.fmt_num,      start)
        self._apply_re(text, self._self_re,     self.fmt_self,     start)
        self._apply_re(text, self._deco_re,     self.fmt_deco,     start)
        self._apply_re(text, self._func_re,     self.fmt_func,     start)
        self._apply_re(text, self._class_re,    self.fmt_class,    start)


        i = start
        while i < n:
            c = text[i]

            # deteksi prefix string f/b/r/u/rb/br
            prefix_end = i
            if c in 'fFbBrRuU' and i + 1 < n:
                nxt = text[i + 1]
                if nxt in ('"', "'"):
                    prefix_end = i + 1
                elif nxt in 'bBrR' and i + 2 < n and text[i + 2] in ('"', "'"):
                    prefix_end = i + 2

            if prefix_end < n and text[prefix_end] in ('"', "'"):
                q = text[prefix_end]

                # triple-quote?
                if text[prefix_end:prefix_end + 3] == q * 3:
                    delim     = q * 3
                    search_at = prefix_end + 3
                    end       = text.find(delim, search_at)

                    if end == -1:
                        # tidak ada penutup di baris ini → multiline
                        self.setFormat(i, n - i, self.fmt_str3)
                        self.setCurrentBlockState(
                            self.TRIPLE_DQ if q == '"' else self.TRIPLE_SQ
                        )
                        return
                    else:
                        # buka dan tutup di baris yang sama
                        self.setFormat(i, end + 3 - i, self.fmt_str3)
                        i = end + 3
                        continue
                else:
                    # string biasa — cari penutup, handle escape
                    j = prefix_end + 1
                    while j < n:
                        if text[j] == '\\':
                            j += 2
                            continue
                        if text[j] == q:
                            break
                        j += 1
                    self.setFormat(i, j + 1 - i, self.fmt_str)
                    i = j + 1
                    continue

            # komentar #
            if c == '#':
                self.setFormat(i, n - i, self.fmt_comment)
                return

            i += 1

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class JavaHighlighter(_BaseHL):
    _KW_JAVA = {
        "abstract","assert","boolean","break","byte","case","catch","char",
        "class","const","continue","default","do","double","else","enum",
        "extends","final","finally","float","for","goto","if","implements",
        "import","instanceof","int","interface","long","native","new",
        "package","private","protected","public","return","short","static",
        "strictfp","super","switch","synchronized","this","throw","throws",
        "transient","try","var","void","volatile","while",
        "true","false","null",
    }

    def __init__(self, document=None):
        super().__init__(document)
        self._java_mode  = False
        self._is_smali   = False
        self.fmt_kw      = self._fmt("#c678dd", bold=True)
        self.fmt_type    = self._fmt("#e5c07b")
        self.fmt_str     = self._fmt("#98c379")
        self.fmt_num     = self._fmt("#d19a66")
        self.fmt_comment = self._fmt("#5c6370", italic=True)
        self.fmt_annot   = self._fmt("#c792ea")
        self.fmt_func    = self._fmt("#61afef")
        self.fmt_smali_r = self._fmt("#e06c75")
        self.fmt_smali_t = self._fmt("#56b6c2")
        self.fmt_var     = self._fmt("#4fc1e9")   # local variables

        self._kw_re        = QRegularExpression(r'\b(' + '|'.join(sorted(self._KW_JAVA)) + r')\b')
        self._type_re      = QRegularExpression(r'\b[A-Z][a-zA-Z0-9_]*\b')
        self._str_re       = QRegularExpression(r'"([^"\\]|\\.)*"')
        self._num_re       = QRegularExpression(r'\b(0[xX][0-9a-fA-F]+L?|\d+\.?\d*[fFdDlL]?)\b')
        self._func_re      = QRegularExpression(r'\b([a-zA-Z_]\w*)\s*(?=\()')
        self._ann_re       = QRegularExpression(r'@[A-Za-z_][\w.]*')
        self._var_re       = QRegularExpression(r'(?<=[A-Z][a-zA-Z0-9_]*\s{1,20}|\bvar\s|\bint\s|\blong\s|\bfloat\s|\bdouble\s|\bboolean\s|\bchar\s|\bbyte\s|\bshort\s)([a-z_][a-zA-Z0-9_]*)(?=\s*[=;,)\[])')
        self._smali_dir_re = QRegularExpression(r'^\s*\.[a-z\-]+')
        self._smali_reg_re = QRegularExpression(r'\b[vp]\d+\b')
        self._smali_typ_re = QRegularExpression(r'L[a-zA-Z][a-zA-Z0-9_/]*;|\[*[ZBSCIJFDV]')
        self._smali_op_re  = QRegularExpression(
            r'\b(invoke-\w+|[a-z]get[-\w]*|[a-z]put[-\w]*|move[\w/-]*|'
            r'return[\w/-]*|const[\w/\-]*|new-\w+|filled-\w+|check-cast|'
            r'instance-of|array-length|throw|goto[\w/\-]*|if-\w+|add-\w+|'
            r'sub-\w+|mul-\w+|div-\w+|rem-\w+|and-\w+|or-\w+|xor-\w+|'
            r'shl-\w+|shr-\w+|ushr-\w+|neg-\w+|not-\w+|monitor-\w+|'
            r'packed-switch|sparse-switch|fill-array-data|aget-\w*|aput-\w*|'
            r'cmpl-\w*|cmpg-\w*|cmp-\w*)\b'
        )

    def _highlight(self, text):
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)
        start = 0
        if prev == self.IN_COMMENT:
            end = text.find("*/")
            if end == -1:
                self.setFormat(0, len(text), self.fmt_comment)
                self.setCurrentBlockState(self.IN_COMMENT)
                return
            self.setFormat(0, end + 2, self.fmt_comment)
            start = end + 2

        if self._is_smali:
            self._scan_smali(text, start)
        else:
            self._scan_java(text, start)

    def _scan_java(self, text, start):
        n = len(text)
        self._apply_re(text, self._var_re,   self.fmt_var,   start, group=1)  # var names after type
        self._apply_re(text, self._kw_re,   self.fmt_kw,    start)
        self._apply_re(text, self._type_re,  self.fmt_type,  start)
        self._apply_re(text, self._num_re,   self.fmt_num,   start)
        self._apply_re(text, self._str_re,   self.fmt_str,   start)
        self._apply_re(text, self._ann_re,   self.fmt_annot, start)
        self._apply_re(text, self._func_re,  self.fmt_func,  start, group=1)
        i = start
        while i < n:
            if text[i:i+2] == "/*":
                end = text.find("*/", i + 2)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_comment)
                    self.setCurrentBlockState(self.IN_COMMENT)
                    return
                self.setFormat(i, end + 2 - i, self.fmt_comment)
                i = end + 2
            elif text[i:i+2] == "//":
                self.setFormat(i, n - i, self.fmt_comment)
                return
            else:
                i += 1

    def _scan_smali(self, text, start):
        n = len(text)
        stripped = text.lstrip()
        if stripped.startswith("#"):
            ci = text.index("#")
            self.setFormat(ci, n - ci, self.fmt_comment)
            return
        self._apply_re(text, self._smali_dir_re, self.fmt_kw,      start)
        self._apply_re(text, self._smali_op_re,  self.fmt_kw,      start)
        self._apply_re(text, self._smali_typ_re,  self.fmt_smali_t, start)
        self._apply_re(text, self._smali_reg_re,  self.fmt_smali_r, start)
        self._apply_re(text, self._str_re,        self.fmt_str,     start)
        self._apply_re(text, self._num_re,        self.fmt_num,     start)
        self._apply_re(text, self._ann_re,        self.fmt_annot,   start)

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class CobolHighlighter(_BaseHL):
    _KW = {
        "ACCEPT","ACCESS","ADD","ADVANCING","AFTER","ALL","ALPHABETIC",
        "ALPHANUMERIC","ALTER","AND","ARE","AREA","AREAS","ASCENDING","ASSIGN",
        "AT","AUTHOR","BEFORE","BINARY","BLANK","BLOCK","BY","CALL","CANCEL",
        "CD","CF","CH","CHARACTER","CHARACTERS","CLASS","CLOSE","COBOL","CODE",
        "COLUMN","COMMA","COMMON","COMMUNICATION","COMP","COMPUTATIONAL",
        "COMPUTE","CONFIGURATION","CONTAINS","CONTENT","CONTINUE","CONTROL",
        "CONTROLS","CONVERTING","COPY","CORR","CORRESPONDING","COUNT","CURRENCY",
        "DATA","DATE","DAY","DE","DEBUG","DECIMAL","DECLARATIVES","DELETE",
        "DELIMITED","DELIMITER","DEPENDING","DESCENDING","DESTINATION","DETAIL",
        "DISABLE","DISPLAY","DIVIDE","DIVISION","DOWN","DUPLICATES","DYNAMIC",
        "ELSE","EMI","ENABLE","END","ENVIRONMENT","EQUAL","ERROR","EVALUATE",
        "EVERY","EXCEPTION","EXIT","EXTEND","EXTERNAL","FALSE","FD","FILE",
        "FILLER","FINAL","FIRST","FOOTING","FOR","FROM","FUNCTION","GENERATE",
        "GIVING","GLOBAL","GO","GREATER","GROUP","HEADING","HIGH","I-O",
        "IDENTIFICATION","IF","IN","INDEX","INDICATE","INITIAL","INITIALIZE",
        "INPUT","INSPECT","INTO","INVALID","IS","JUST","JUSTIFIED","KEY",
        "LABEL","LEADING","LEFT","LENGTH","LESS","LIMIT","LINE","LINES",
        "LINKAGE","LOCAL","LOCK","LOW","MEMORY","MERGE","MESSAGE","MODE","MOVE",
        "MULTIPLY","NATIVE","NEGATIVE","NEXT","NOT","NULL","NUMBER","NUMERIC",
        "OBJECT","OCCURS","OF","OFF","OMITTED","ON","OPEN","OR","ORDER",
        "ORGANIZATION","OTHER","OUTPUT","OVERFLOW","PACKED","PERFORM","PF",
        "PH","PIC","PICTURE","PLUS","POINTER","POSITIVE","PROCEDURE","PROGRAM",
        "PURGE","QUEUE","RANDOM","READ","RECEIVE","RECORD","REDEFINES","REEL",
        "REFERENCE","RELATIVE","RELEASE","REMAINDER","REMOVAL","RENAMES",
        "REPLACE","REPLACING","REPORT","RERUN","RESERVE","RESET","RETURN",
        "REVERSED","REWIND","REWRITE","RF","RH","RIGHT","ROUNDED","RUN","SAME",
        "SEARCH","SECTION","SELECT","SEND","SENTENCE","SEPARATE","SEQUENCE",
        "SEQUENTIAL","SET","SIGN","SIZE","SORT","SOURCE","SPACE","SPECIAL",
        "STANDARD","STATUS","STOP","STRING","SUBTRACT","SUM","SUPPRESS","SYMBOLIC",
        "SYNCHRONIZED","TABLE","TALLYING","TAPE","TERMINAL","TEST","TEXT","THAN",
        "THEN","THROUGH","THRU","TIME","TIMES","TO","TOP","TRAILING","TRUE",
        "TYPE","UNIT","UNSTRING","UNTIL","UP","UPON","USAGE","USE","USING",
        "VALUE","VALUES","VARYING","WHEN","WITH","WORDS","WORKING","WRITE","ZERO",
    }

    def __init__(self, document=None):
        super().__init__(document)
        self._cobol_mode = False
        self.fmt_kw      = self._fmt("#c678dd", bold=True)
        self.fmt_div     = self._fmt("#61afef", bold=True)   # DIVISION/SECTION
        self.fmt_str     = self._fmt("#98c379")
        self.fmt_num     = self._fmt("#d19a66")
        self.fmt_comment = self._fmt("#5c6370", italic=True)
        self.fmt_pic     = self._fmt("#56b6c2")              # PIC clause
        self.fmt_level   = self._fmt("#e5c07b", bold=True)   # level numbers 01 05 77

        self._kw_re    = QRegularExpression(r'\b(' + '|'.join(sorted(self._KW)) + r')\b')
        self._div_re   = QRegularExpression(r'\b(DIVISION|SECTION)\b')
        self._str_re   = QRegularExpression(r'"[^"]*"|\'[^\']*\'')
        self._num_re   = QRegularExpression(r'\b\d+\.?\d*\b')
        self._level_re = QRegularExpression(r'^\s*(0[1-9]|[1-7]\d|77|66|88)\b')
        self._pic_re   = QRegularExpression(r'\bPIC(?:TURE)?\s+(?:IS\s+)?[X9A()\-+.,/BPSVZn0]+', QRegularExpression.CaseInsensitiveOption)

    def _highlight(self, text):
        self.setCurrentBlockState(0)
        n = len(text)

        # COBOL kolom 7 = komentar (kolom 1-indexed)
        stripped = text.lstrip()
        # jika karakter ke-7 (index 6) adalah * atau /, itu comment line
        if len(text) >= 7 and text[6] in ('*', '/'):
            self.setFormat(0, n, self.fmt_comment)
            return
        # atau baris yang diawali * setelah leading space (format bebas)
        if stripped.startswith('*'):
            self.setFormat(0, n, self.fmt_comment)
            return

        self._apply_re(text, self._level_re, self.fmt_level)
        self._apply_re(text, self._kw_re,    self.fmt_kw)
        self._apply_re(text, self._div_re,   self.fmt_div)
        self._apply_re(text, self._pic_re,   self.fmt_pic)
        self._apply_re(text, self._num_re,   self.fmt_num)
        self._apply_re(text, self._str_re,   self.fmt_str)

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class AsmHighlighter(_BaseHL):
    # x86 instructions
    _X86_OPS = {
        "mov","push","pop","call","ret","retn","retf","jmp","je","jne","jz","jnz",
        "jl","jle","jg","jge","ja","jae","jb","jbe","js","jns","jo","jno","jc","jnc",
        "jp","jnp","jpe","jpo","jecxz","jcxz","loop","loope","loopne","loopz","loopnz",
        "add","sub","mul","imul","div","idiv","inc","dec","neg","not","and","or","xor",
        "shl","shr","sal","sar","rol","ror","rcl","rcr","test","cmp","lea","xchg",
        "nop","hlt","int","iret","iretd","sti","cli","cld","std","stc","clc","cmc",
        "pushf","popf","pushfd","popfd","pusha","popa","pushad","popad",
        "movsx","movzx","cbw","cwd","cdq","cwde","bswap","bsf","bsr","bt","bts","btr","btc",
        "sete","setne","setl","setle","setg","setge","seta","setae","setb","setbe",
        "cmove","cmovne","cmovl","cmovle","cmovg","cmovge","cmova","cmovae","cmovb","cmovbe",
        "rep","repe","repne","repz","repnz","movsb","movsw","movsd","stosb","stosw","stosd",
        "lodsb","lodsw","lodsd","scasb","scasw","scasd","cmpsb","cmpsw","cmpsd",
        "enter","leave","in","out","ins","outs","lds","les","lfs","lgs","lss",
        "adc","sbb","daa","das","aaa","aas","aam","aad","xlat","lahf","sahf",
        "lgdt","lidt","lldt","ltr","sgdt","sidt","sldt","str","lmsw","smsw",
        "cpuid","rdtsc","rdmsr","wrmsr","invd","wbinvd","invlpg","lock","wait","fwait",
        # SSE/MMX common
        "movaps","movups","movss","addss","subss","mulss","divss","xorps","andps",
        "movdqu","movdqa","pxor","paddq","pand","por","pandn","pcmpeqb",
    }
    # ARM instructions
    _ARM_OPS = {
        "ldr","str","ldm","stm","push","pop","mov","mvn","add","sub","rsb","adc",
        "sbc","rsc","mul","mla","mls","umull","umlal","smull","smlal",
        "and","orr","eor","bic","orn","tst","teq","cmp","cmn",
        "b","bl","bx","blx","bne","beq","bgt","blt","bge","ble","bhi","blo","bhs","bls",
        "lsl","lsr","asr","ror","rrx","clz","rev","rev16","revsh",
        "it","ite","itt","itee","itet","itte","ittet","ittte","ittee","iteee","itttt",
        "nop","svc","bkpt","wfi","wfe","sev","yield","isb","dsb","dmb",
        "vldr","vstr","vmov","vadd","vsub","vmul","vdiv","vcmp","vcvt",
        "ldrb","ldrh","ldrsb","ldrsh","strb","strh","ldrex","strex",
        "msr","mrs","cps","setend","pld","pli","pld","clrex",
    }
    # registers
    _X86_REGS = {
        "eax","ebx","ecx","edx","esi","edi","esp","ebp","eip","eflags",
        "ax","bx","cx","dx","si","di","sp","bp","ip",
        "al","ah","bl","bh","cl","ch","dl","dh",
        "rax","rbx","rcx","rdx","rsi","rdi","rsp","rbp","rip",
        "r8","r9","r10","r11","r12","r13","r14","r15",
        "r8d","r9d","r10d","r11d","r12d","r13d","r14d","r15d",
        "cs","ds","es","fs","gs","ss",
        "cr0","cr2","cr3","cr4","dr0","dr1","dr2","dr3","dr6","dr7",
        "xmm0","xmm1","xmm2","xmm3","xmm4","xmm5","xmm6","xmm7",
        "ymm0","ymm1","ymm2","ymm3","ymm4","ymm5","ymm6","ymm7",
        "mm0","mm1","mm2","mm3","mm4","mm5","mm6","mm7",
        "st0","st1","st2","st3","st4","st5","st6","st7",
    }
    _ARM_REGS = {
        "r0","r1","r2","r3","r4","r5","r6","r7",
        "r8","r9","r10","r11","r12","r13","r14","r15",
        "sp","lr","pc","cpsr","spsr","fp","ip",
        "s0","s1","s2","s3","s4","s5","s6","s7",
        "d0","d1","d2","d3","d4","d5","d6","d7",
        "q0","q1","q2","q3","q4","q5","q6","q7",
    }
    _DIRECTIVES = {
        "section","segment","global","extern","db","dw","dd","dq","dt","do",
        "byte","word","dword","qword","tword","oword","yword","zword",
        "resb","resw","resd","resq","rest","reso","resy","resz",
        "equ","times","align","alignb","use16","use32","use64",
        "bits","org","incbin","include","struc","endstruc","istruc","iend",
        "macro","endmacro","local","common","absolute","cpu","float",
        ".text",".data",".bss",".rodata",".code",".stack",
        "proc","endp","assume","model","end","public","private",
        ".global",".extern",".section",".text",".data",".bss",".rodata",
        ".byte",".word",".long",".quad",".ascii",".asciz",".string",
        ".zero",".space",".align",".balign",".fill",".rept",".endr",
        ".equ",".set",".include",".if",".else",".endif",".macro",".endm",
        ".thumb",".arm",".syntax","unified","divided",
    }

    def __init__(self, document=None):
        super().__init__(document)
        self._asm_mode   = False
        self.fmt_op      = self._fmt("#61afef", bold=True)   # mnemonic
        self.fmt_reg     = self._fmt("#e06c75")               # register
        self.fmt_dir     = self._fmt("#c678dd")               # directive
        self.fmt_num     = self._fmt("#d19a66")               # number
        self.fmt_str     = self._fmt("#98c379")               # string
        self.fmt_comment = self._fmt("#5c6370", italic=True)  # ; or #
        self.fmt_label   = self._fmt("#e5c07b", bold=True)    # label:
        self.fmt_macro   = self._fmt("#c792ea")               # %macro %define

        all_ops = self._X86_OPS | self._ARM_OPS
        all_regs = self._X86_REGS | self._ARM_REGS

        self._op_re    = QRegularExpression(r'(?i)\b(' + '|'.join(sorted(all_ops)) + r')\b')
        self._reg_re   = QRegularExpression(r'(?i)\b(' + '|'.join(sorted(all_regs)) + r')\b')
        self._dir_re   = QRegularExpression(
            r'(?i)(?:^\s*|\s)(' + '|'.join(re.escape(d) for d in sorted(self._DIRECTIVES, key=len, reverse=True)) + r')\b'
        )
        self._num_re   = QRegularExpression(r'\b(0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|\d+[hHbBoOdD]?)\b')
        self._str_re   = QRegularExpression(r'"[^"]*"|\'[^\']*\'')
        self._label_re = QRegularExpression(r'^\s*\.?[a-zA-Z_][a-zA-Z0-9_]*\s*:')
        self._macro_re = QRegularExpression(r'%[a-zA-Z_]\w*')

    def _highlight(self, text):
        self.setCurrentBlockState(0)
        n = len(text)
        stripped = text.lstrip()

        # comment: ; (x86) or @ (ARM) or # (GNU AS)
        for ch in (';', '@', '#'):
            if ch in text:
                ci = text.index(ch)
                # ensure not inside string
                before = text[:ci]
                if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                    self.setFormat(ci, n - ci, self.fmt_comment)
                    text = text[:ci]
                    n = ci
                    break

        self._apply_re(text, self._label_re, self.fmt_label)
        self._apply_re(text, self._dir_re,   self.fmt_dir, group=1)
        self._apply_re(text, self._op_re,    self.fmt_op)
        self._apply_re(text, self._reg_re,   self.fmt_reg)
        self._apply_re(text, self._num_re,   self.fmt_num)
        self._apply_re(text, self._str_re,   self.fmt_str)
        self._apply_re(text, self._macro_re, self.fmt_macro)

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class KotlinHighlighter(_BaseHL):
    _KW = {
        "as","break","class","continue","do","else","false","for","fun","if",
        "in","interface","is","null","object","package","return","super","this",
        "throw","true","try","typealias","typeof","val","var","when","while",
        "by","catch","constructor","delegate","dynamic","field","file","finally",
        "get","import","init","param","property","receiver","set","setparam",
        "where","abstract","actual","annotation","companion","crossinline",
        "data","enum","expect","external","final","infix","inline","inner",
        "internal","lateinit","noinline","open","operator","out","override",
        "private","protected","public","reified","sealed","suspend","tailrec",
        "vararg","it","Unit","Nothing","Any","Boolean","Byte","Short","Int",
        "Long","Float","Double","Char","String","Array","List","Map","Set",
        "MutableList","MutableMap","MutableSet","Pair","Triple","Sequence",
        "Flow","StateFlow","SharedFlow","Coroutine","CoroutineScope",
    }

    TRIPLE_DQ = 4

    def __init__(self, document=None):
        super().__init__(document)
        self._kotlin_mode = False
        self.fmt_kw      = self._fmt("#c678dd", bold=True)
        self.fmt_type    = self._fmt("#e5c07b")
        self.fmt_str     = self._fmt("#98c379")
        self.fmt_str3    = self._fmt("#98c379", italic=True)
        self.fmt_num     = self._fmt("#d19a66")
        self.fmt_comment = self._fmt("#5c6370", italic=True)
        self.fmt_annot   = self._fmt("#c792ea")
        self.fmt_func    = self._fmt("#61afef")
        self.fmt_tmpl    = self._fmt("#56b6c2")   # ${...} template
        self.fmt_var     = self._fmt("#4fc1e9")  # local variables

        self._kw_re    = QRegularExpression(r'\b(' + '|'.join(sorted(self._KW)) + r')\b')
        self._type_re  = QRegularExpression(r'\b[A-Z][a-zA-Z0-9_]*\b')
        self._func_re  = QRegularExpression(r'(?<=fun )\s*([a-zA-Z_]\w*)')
        self._num_re   = QRegularExpression(r'\b(0[xX][0-9a-fA-F]+L?|\d+\.?\d*[fFdDlLuU]*)\b')
        self._ann_re   = QRegularExpression(r'@[A-Za-z_][\w.]*')
        self._tmpl_re  = QRegularExpression(r'\$\{[^}]*\}|\$[a-zA-Z_]\w*')
        self._str_re   = QRegularExpression(r'"([^"\\]|\\.)*"')
        self._var_re   = QRegularExpression(r'(?<=\bval |\bvar )([a-z_]\w*)(?=\s*[=:,\n])|(?<=\bfor \()([a-z_]\w*)(?=\s+in\b)')

    def _highlight(self, text):
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)

        if prev == self.TRIPLE_DQ:
            end = text.find('"""')
            if end == -1:
                self.setFormat(0, len(text), self.fmt_str3)
                self.setCurrentBlockState(self.TRIPLE_DQ)
                return
            self.setFormat(0, end + 3, self.fmt_str3)
            self._scan_kt(text, end + 3)
            return

        self._scan_kt(text, 0)

    def _scan_kt(self, text, start):
        n = len(text)
        self._apply_re(text, self._var_re,   self.fmt_var,   start, group=1)  # val/var/for targets
        self._apply_re(text, self._kw_re,   self.fmt_kw,   start)
        self._apply_re(text, self._type_re,  self.fmt_type,  start)
        self._apply_re(text, self._num_re,   self.fmt_num,   start)
        self._apply_re(text, self._ann_re,   self.fmt_annot, start)
        self._apply_re(text, self._func_re,  self.fmt_func,  start)

        i = start
        while i < n:
            if text[i:i+3] == '"""':
                end = text.find('"""', i + 3)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_str3)
                    self.setCurrentBlockState(self.TRIPLE_DQ)
                    return
                self.setFormat(i, end + 3 - i, self.fmt_str3)
                i = end + 3
                continue
            if text[i] == '"':
                j = i + 1
                while j < n:
                    if text[j] == '\\': j += 2; continue
                    if text[j] == '"': break
                    j += 1
                self.setFormat(i, j + 1 - i, self.fmt_str)
                self._apply_re(text[i:j+1], self._tmpl_re, self.fmt_tmpl)
                i = j + 1
                continue
            if text[i:i+2] == "/*":
                end = text.find("*/", i + 2)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_comment)
                    self.setCurrentBlockState(self.IN_COMMENT)
                    return
                self.setFormat(i, end + 2 - i, self.fmt_comment)
                i = end + 2
                continue
            if text[i:i+2] == "//":
                self.setFormat(i, n - i, self.fmt_comment)
                return
            i += 1

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class SwiftHighlighter(_BaseHL):
    _KW = {
        "associatedtype","class","deinit","enum","extension","fileprivate","func",
        "import","init","inout","internal","let","open","operator","private",
        "precedencegroup","protocol","public","rethrows","static","struct",
        "subscript","typealias","var","break","case","catch","continue","default",
        "defer","do","else","fallthrough","for","guard","if","in","repeat",
        "return","throw","switch","where","while","Any","as","await","catch",
        "false","is","nil","rethrows","self","Self","super","throw","throws","true","try",
        "associativity","convenience","didSet","dynamic","final","get","indirect",
        "infix","lazy","left","mutating","none","nonmutating","optional","override",
        "postfix","precedence","prefix","Protocol","required","right","set",
        "some","Type","unowned","weak","willSet",
        # types
        "Int","Int8","Int16","Int32","Int64","UInt","UInt8","UInt16","UInt32","UInt64",
        "Float","Double","Bool","String","Character","Void","Never","Optional",
        "Array","Dictionary","Set","Tuple","Range","ClosedRange","Substring",
        "Data","URL","Date","UUID","Result","Error","AnyError",
    }

    TRIPLE_DQ = 4

    def __init__(self, document=None):
        super().__init__(document)
        self._swift_mode = False
        self.fmt_kw      = self._fmt("#c678dd", bold=True)
        self.fmt_type    = self._fmt("#e5c07b")
        self.fmt_str     = self._fmt("#98c379")
        self.fmt_str3    = self._fmt("#98c379", italic=True)
        self.fmt_num     = self._fmt("#d19a66")
        self.fmt_comment = self._fmt("#5c6370", italic=True)
        self.fmt_annot   = self._fmt("#c792ea")    # @attribute
        self.fmt_func    = self._fmt("#61afef")
        self.fmt_interp  = self._fmt("#56b6c2")    # \(...)
        self.fmt_var     = self._fmt("#4fc1e9")  # local variables

        self._kw_re   = QRegularExpression(r'\b(' + '|'.join(sorted(self._KW)) + r')\b')
        self._type_re = QRegularExpression(r'\b[A-Z][a-zA-Z0-9_]*\b')
        self._func_re = QRegularExpression(r'(?<=func )\s*([a-zA-Z_]\w*)')
        self._num_re  = QRegularExpression(r'\b(0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|\d+\.?\d*)\b')
        self._ann_re  = QRegularExpression(r'@[A-Za-z_]\w*')
        self._itp_re  = QRegularExpression(r'\\\([^)]*\)')
        self._var_re  = QRegularExpression(r'(?<=\blet |\bvar |\bfor |\bguard let |\bif let |\bguard var |\bif var )([a-z_]\w*)(?=\s*[=:,\n]|\s+in\b)')

    def _highlight(self, text):
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)

        if prev == self.TRIPLE_DQ:
            end = text.find('"""')
            if end == -1:
                self.setFormat(0, len(text), self.fmt_str3)
                self.setCurrentBlockState(self.TRIPLE_DQ)
                return
            self.setFormat(0, end + 3, self.fmt_str3)
            self._scan_sw(text, end + 3)
            return

        self._scan_sw(text, 0)

    def _scan_sw(self, text, start):
        n = len(text)
        self._apply_re(text, self._var_re,   self.fmt_var,   start, group=1)  # let/var/for targets
        self._apply_re(text, self._kw_re,   self.fmt_kw,    start)
        self._apply_re(text, self._type_re,  self.fmt_type,  start)
        self._apply_re(text, self._num_re,   self.fmt_num,   start)
        self._apply_re(text, self._ann_re,   self.fmt_annot, start)
        self._apply_re(text, self._func_re,  self.fmt_func,  start)

        i = start
        while i < n:
            if text[i:i+3] == '"""':
                end = text.find('"""', i + 3)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_str3)
                    self.setCurrentBlockState(self.TRIPLE_DQ)
                    return
                self.setFormat(i, end + 3 - i, self.fmt_str3)
                i = end + 3
                continue
            if text[i] == '"':
                j = i + 1
                while j < n:
                    if text[j] == '\\': j += 2; continue
                    if text[j] == '"': break
                    j += 1
                self.setFormat(i, j + 1 - i, self.fmt_str)
                i = j + 1
                continue
            if text[i:i+2] == "/*":
                end = text.find("*/", i + 2)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_comment)
                    self.setCurrentBlockState(self.IN_COMMENT)
                    return
                self.setFormat(i, end + 2 - i, self.fmt_comment)
                i = end + 2
                continue
            if text[i:i+2] == "//":
                self.setFormat(i, n - i, self.fmt_comment)
                return
            i += 1

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class RustHighlighter(_BaseHL):
    _KW = {
        "as","async","await","break","const","continue","crate","dyn","else",
        "enum","extern","false","fn","for","if","impl","in","let","loop","match",
        "mod","move","mut","pub","ref","return","self","Self","static","struct",
        "super","trait","true","type","union","unsafe","use","where","while",
        "abstract","become","box","do","final","macro","override","priv","try",
        "typeof","unsized","virtual","yield",
        # types
        "i8","i16","i32","i64","i128","isize","u8","u16","u32","u64","u128","usize",
        "f32","f64","bool","char","str","String","Vec","Box","Rc","Arc","Cell",
        "RefCell","Option","Result","Ok","Err","Some","None","HashMap","HashSet",
        "BTreeMap","BTreeSet","VecDeque","LinkedList","Cow","Pin","Mutex","RwLock",
    }

    def __init__(self, document=None):
        super().__init__(document)
        self._rust_mode  = False
        self.fmt_kw      = self._fmt("#c678dd", bold=True)
        self.fmt_type    = self._fmt("#e5c07b")
        self.fmt_str     = self._fmt("#98c379")
        self.fmt_num     = self._fmt("#d19a66")
        self.fmt_comment = self._fmt("#5c6370", italic=True)
        self.fmt_macro   = self._fmt("#56b6c2")   # println! macro!
        self.fmt_ltime   = self._fmt("#c792ea")   # 'lifetime
        self.fmt_func    = self._fmt("#61afef")
        self.fmt_attr    = self._fmt("#c792ea")   # #[attribute]
        self.fmt_var     = self._fmt("#4fc1e9")  # let/for bindings

        self._kw_re    = QRegularExpression(r'\b(' + '|'.join(sorted(self._KW)) + r')\b')
        self._type_re  = QRegularExpression(r'\b[A-Z][a-zA-Z0-9_]*\b')
        self._func_re  = QRegularExpression(r'(?<=fn )\s*([a-zA-Z_]\w*)')
        self._num_re   = QRegularExpression(r'\b(0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*\.?[\d_]*(?:[eE][+-]?\d+)?(?:_?[a-z0-9]+)?)\b')
        self._macro_re = QRegularExpression(r'\b[a-z_]\w*!')
        self._ltime_re = QRegularExpression(r"'[a-z_]\w*\b")
        self._attr_re  = QRegularExpression(r'#!\[.*?\]|#\[.*?\]')  # #[attr] and #![crate-attr]
        self._str_re   = QRegularExpression(r'r#*"[^"]*"#*|"([^"\\]|\\.)*"')  # raw strings + normal
        self._chr_re   = QRegularExpression(r"'([^'\\]|\\.)'")
        self._var_re   = QRegularExpression(r'(?<=\blet |\blet mut |\bfor |\bref )([a-z_]\w*)(?=\s*[=:,;]|\s+in\b)')

    def _highlight(self, text):
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)

        if prev == self.IN_COMMENT:
            end = text.find("*/")
            if end == -1:
                self.setFormat(0, len(text), self.fmt_comment)
                self.setCurrentBlockState(self.IN_COMMENT)
                return
            self.setFormat(0, end + 2, self.fmt_comment)
            self._scan_rs(text, end + 2)
            return

        self._scan_rs(text, 0)

    def _scan_rs(self, text, start):
        n = len(text)
        self._apply_re(text, self._var_re,    self.fmt_var,    start, group=1)  # let/for bindings
        self._apply_re(text, self._kw_re,    self.fmt_kw,     start)
        self._apply_re(text, self._type_re,   self.fmt_type,   start)
        self._apply_re(text, self._num_re,    self.fmt_num,    start)
        self._apply_re(text, self._macro_re,  self.fmt_macro,  start)
        self._apply_re(text, self._ltime_re,  self.fmt_ltime,  start)
        self._apply_re(text, self._attr_re,   self.fmt_attr,   start)
        self._apply_re(text, self._func_re,   self.fmt_func,   start)
        self._apply_re(text, self._str_re,    self.fmt_str,    start)
        self._apply_re(text, self._chr_re,    self.fmt_str,    start)

        i = start
        while i < n:
            if text[i:i+2] == "/*":
                end = text.find("*/", i + 2)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_comment)
                    self.setCurrentBlockState(self.IN_COMMENT)
                    return
                self.setFormat(i, end + 2 - i, self.fmt_comment)
                i = end + 2
                continue
            if text[i:i+2] == "//":
                self.setFormat(i, n - i, self.fmt_comment)
                return
            i += 1

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class BashHighlighter(_BaseHL):
    _KW = {
        "if","then","else","elif","fi","for","while","do","done","case","esac",
        "in","until","select","function","return","exit","break","continue",
        "local","declare","typeset","export","readonly","unset","shift",
        "trap","eval","exec","source","alias","unalias","jobs","fg","bg",
        "wait","kill","true","false","test","let","read","echo","printf",
        "set","unset","shopt","getopts","enable","builtin","command","type",
        "cd","pwd","pushd","popd","dirs","ls","cp","mv","rm","mkdir","rmdir",
        "chmod","chown","chgrp","ln","find","grep","sed","awk","cut","sort",
        "uniq","wc","head","tail","cat","tee","pipe","xargs","tr","basename",
        "dirname","realpath","readlink","stat","file","which","whereis",
        "expr","bc","printf","date","sleep","time","timeout","nohup",
    }

    def __init__(self, document=None):
        super().__init__(document)
        self._bash_mode  = False
        self.fmt_kw      = self._fmt("#c678dd", bold=True)
        self.fmt_str     = self._fmt("#98c379")
        self.fmt_str2    = self._fmt("#98c379", italic=True)   # single quote (no interp)
        self.fmt_num     = self._fmt("#d19a66")
        self.fmt_comment = self._fmt("#5c6370", italic=True)
        self.fmt_var     = self._fmt("#56b6c2")    # $VAR ${VAR} — usage
        self.fmt_decl    = self._fmt("#4fc1e9")  # VARNAME= — declaration
        self.fmt_cmd     = self._fmt("#61afef")    # $(cmd) `cmd`
        self.fmt_shebang = self._fmt("#e5c07b", bold=True)   # #!/...
        self.fmt_flag    = self._fmt("#e5c07b")    # -flag --flag

        self._kw_re     = QRegularExpression(r'\b(' + '|'.join(sorted(self._KW)) + r')\b')
        self._var_re    = QRegularExpression(r'\$\{?[a-zA-Z_][a-zA-Z0-9_]*\}?|\$[0-9#@*?$!-]')
        self._decl_re   = QRegularExpression(r'^\s*(?:local\s+|declare\s+(?:-[a-zA-Z]+\s+)?|export\s+|readonly\s+)?([A-Za-z_][A-Za-z0-9_]*)(?==)')
        self._cmd_re    = QRegularExpression(r'\$\([^)]*\)|`[^`]*`')
        self._num_re    = QRegularExpression(r'\b\d+\b')
        self._flag_re   = QRegularExpression(r'(?<!\S)--?[a-zA-Z][a-zA-Z0-9-]*')
        self._shebang_re= QRegularExpression(r'^#!')

    def _highlight(self, text):
        self.setCurrentBlockState(0)
        n = len(text)

        # shebang
        if text.startswith('#!'):
            self.setFormat(0, n, self.fmt_shebang)
            return

        # scan char by char for strings and comments
        self._apply_re(text, self._kw_re,  self.fmt_kw)
        self._apply_re(text, self._decl_re, self.fmt_decl, group=1)  # VAR= declaration lhs
        self._apply_re(text, self._num_re,  self.fmt_num)
        self._apply_re(text, self._flag_re, self.fmt_flag)

        i = 0
        while i < n:
            c = text[i]
            # double-quote string
            if c == '"':
                j = i + 1
                while j < n:
                    if text[j] == '\\': j += 2; continue
                    if text[j] == '"': break
                    j += 1
                self.setFormat(i, j + 1 - i, self.fmt_str)
                # $VAR inside double-quote
                segment = text[i:j+1]
                it2 = self._var_re.globalMatch(segment)
                while it2.hasNext():
                    m = it2.next()
                    self.setFormat(i + m.capturedStart(), m.capturedLength(), self.fmt_var)
                i = j + 1
                continue
            # single-quote (no interpolation)
            if c == "'":
                j = text.find("'", i + 1)
                if j == -1: j = n - 1
                self.setFormat(i, j + 1 - i, self.fmt_str2)
                i = j + 1
                continue
            # $(...) or `...`
            if text[i:i+2] == "$(" or c == '`':
                close = ')' if c != '`' else '`'
                start_i = i + (2 if text[i:i+2] == "$(" else 1)
                j = text.find(close, start_i)
                if j == -1: j = n - 1
                length = j + 1 - i
                self.setFormat(i, length, self.fmt_cmd)
                i = j + 1
                continue
            # $VAR
            if c == '$':
                m = self._var_re.match(text, i)
                if m.hasMatch() and m.capturedStart() == i:
                    self.setFormat(i, m.capturedLength(), self.fmt_var)
                    i += m.capturedLength()
                    continue
            # comment
            if c == '#':
                self.setFormat(i, n - i, self.fmt_comment)
                return
            i += 1

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class PhpHighlighter(_BaseHL):
    _KW = {
        "abstract","and","array","as","break","callable","case","catch","class",
        "clone","const","continue","declare","default","die","do","echo","else",
        "elseif","empty","enddeclare","endfor","endforeach","endif","endswitch",
        "endwhile","eval","exit","extends","final","finally","fn","for","foreach",
        "function","global","goto","if","implements","include","include_once",
        "instanceof","insteadof","interface","isset","list","match","namespace",
        "new","or","print","private","protected","public","readonly","require",
        "require_once","return","static","switch","throw","trait","try","unset",
        "use","var","while","xor","yield","__halt_compiler",
        "true","false","null","TRUE","FALSE","NULL",
        "self","parent","static","__CLASS__","__DIR__","__FILE__","__FUNCTION__",
        "__LINE__","__METHOD__","__NAMESPACE__","__TRAIT__",
    }

    def __init__(self, document=None):
        super().__init__(document)
        self._php_mode   = False
        self.fmt_kw      = self._fmt("#c678dd", bold=True)
        self.fmt_type    = self._fmt("#e5c07b")
        self.fmt_str     = self._fmt("#98c379")
        self.fmt_num     = self._fmt("#d19a66")
        self.fmt_comment = self._fmt("#5c6370", italic=True)
        self.fmt_var     = self._fmt("#4fc1e9")  # $variable
        self.fmt_func    = self._fmt("#61afef")
        self.fmt_tag     = self._fmt("#c792ea")    # <?php ?>

        self._kw_re   = QRegularExpression(r'\b(' + '|'.join(sorted(self._KW)) + r')\b')
        self._type_re = QRegularExpression(r'\b[A-Z][a-zA-Z0-9_]*\b')
        self._var_re  = QRegularExpression(r'\$[a-zA-Z_][a-zA-Z0-9_]*')
        self._func_re = QRegularExpression(r'\b([a-zA-Z_]\w*)\s*(?=\()')
        self._num_re  = QRegularExpression(r'\b(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+\.?\d*[eE]?[+-]?\d*)\b')
        self._tag_re  = QRegularExpression(r'<\?php|<\?=|\?>')
        self._str_re  = QRegularExpression(r'"([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\'')

    def _highlight(self, text):
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)

        start = 0
        if prev == self.IN_COMMENT:
            end = text.find("*/")
            if end == -1:
                self.setFormat(0, len(text), self.fmt_comment)
                self.setCurrentBlockState(self.IN_COMMENT)
                return
            self.setFormat(0, end + 2, self.fmt_comment)
            start = end + 2

        n = len(text)
        self._apply_re(text, self._tag_re,  self.fmt_tag,   start)
        self._apply_re(text, self._kw_re,   self.fmt_kw,    start)
        self._apply_re(text, self._type_re,  self.fmt_type,  start)
        self._apply_re(text, self._var_re,   self.fmt_var,   start)
        self._apply_re(text, self._num_re,   self.fmt_num,   start)
        self._apply_re(text, self._str_re,   self.fmt_str,   start)
        self._apply_re(text, self._func_re,  self.fmt_func,  start, group=1)

        i = start
        while i < n:
            if text[i:i+2] == "/*":
                end = text.find("*/", i + 2)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_comment)
                    self.setCurrentBlockState(self.IN_COMMENT)
                    return
                self.setFormat(i, end + 2 - i, self.fmt_comment)
                i = end + 2
            elif text[i:i+2] == "//" or text[i] == "#":
                self.setFormat(i, n - i, self.fmt_comment)
                return
            else:
                i += 1

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)


class CssHighlighter(_BaseHL):
    _PROPS = {
        "align-content","align-items","align-self","all","animation","appearance",
        "background","background-color","background-image","background-position",
        "background-repeat","background-size","border","border-bottom","border-color",
        "border-left","border-radius","border-right","border-style","border-top",
        "border-width","bottom","box-shadow","box-sizing","clear","clip","color",
        "column-gap","content","cursor","display","filter","flex","flex-direction",
        "flex-flow","flex-grow","flex-shrink","flex-wrap","float","font","font-family",
        "font-size","font-style","font-weight","gap","grid","grid-area","grid-column",
        "grid-row","grid-template","height","justify-content","justify-items",
        "justify-self","left","letter-spacing","line-height","list-style","margin",
        "margin-bottom","margin-left","margin-right","margin-top","max-height",
        "max-width","min-height","min-width","object-fit","opacity","order",
        "outline","overflow","overflow-x","overflow-y","padding","padding-bottom",
        "padding-left","padding-right","padding-top","pointer-events","position",
        "right","row-gap","text-align","text-decoration","text-overflow","text-shadow",
        "text-transform","top","transform","transition","user-select","vertical-align",
        "visibility","white-space","width","word-break","word-wrap","z-index",
    }
    _VALUES = {
        "auto","none","inherit","initial","unset","revert","block","inline","flex",
        "grid","inline-flex","inline-block","inline-grid","absolute","relative",
        "fixed","sticky","static","normal","bold","italic","center","left","right",
        "top","bottom","middle","baseline","nowrap","wrap","row","column",
        "space-between","space-around","space-evenly","stretch","transparent",
        "solid","dashed","dotted","double","hidden","visible","scroll","clip",
        "border-box","content-box","cover","contain","no-repeat","repeat",
        "pointer","default","text","move","not-allowed","grab","grabbing",
        "uppercase","lowercase","capitalize","underline","line-through","overline",
        "sans-serif","serif","monospace","cursive","fantasy","px","em","rem",
        "vh","vw","vmin","vmax","%","fr","deg","rad","turn","s","ms",
    }

    def __init__(self, document=None):
        super().__init__(document)
        self._css_mode   = False
        self.fmt_sel     = self._fmt("#e06c75")               # selector
        self.fmt_prop    = self._fmt("#61afef")               # property
        self.fmt_val     = self._fmt("#98c379")               # value
        self.fmt_num     = self._fmt("#d19a66")               # number + unit
        self.fmt_color   = self._fmt("#d19a66")               # #hex color
        self.fmt_str     = self._fmt("#98c379")               # "string"
        self.fmt_comment = self._fmt("#5c6370", italic=True)  # /* */
        self.fmt_at      = self._fmt("#c678dd", bold=True)    # @media @keyframes
        self.fmt_var     = self._fmt("#4fc1e9")             # --var / var()
        self.fmt_pseudo  = self._fmt("#c792ea")               # :hover ::before
        self.fmt_bracket = self._fmt("#e5c07b")               # { }

        self._prop_re   = QRegularExpression(r'(?<=\{|;|\n)\s*(' + '|'.join(sorted(self._PROPS, key=len, reverse=True)) + r')\s*(?=:)', QRegularExpression.CaseInsensitiveOption)
        self._at_re     = QRegularExpression(r'@[a-zA-Z-]+')
        self._hex_re    = QRegularExpression(r'#[0-9a-fA-F]{3,8}\b')
        self._num_re    = QRegularExpression(r'-?\d+\.?\d*(?:px|em|rem|vh|vw|vmin|vmax|%|fr|deg|rad|turn|s|ms|ch|ex|cm|mm|in|pt|pc)?\b')
        self._var_re    = QRegularExpression(r'--[a-zA-Z][a-zA-Z0-9-]*|var\([^)]*\)')
        self._pseudo_re = QRegularExpression(r':{1,2}[a-zA-Z-]+')
        self._str_re    = QRegularExpression(r'"[^"]*"|\'[^\']*\'')
        self._sel_re    = QRegularExpression(r'[.#]?[a-zA-Z][a-zA-Z0-9_-]*(?=\s*\{)')
        self._brace_re  = QRegularExpression(r'[{}]')

    def _highlight(self, text):
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)

        start = 0
        if prev == self.IN_COMMENT:
            end = text.find("*/")
            if end == -1:
                self.setFormat(0, len(text), self.fmt_comment)
                self.setCurrentBlockState(self.IN_COMMENT)
                return
            self.setFormat(0, end + 2, self.fmt_comment)
            start = end + 2

        n = len(text)
        self._apply_re(text, self._sel_re,    self.fmt_sel,     start)
        self._apply_re(text, self._at_re,     self.fmt_at,      start)
        self._apply_re(text, self._prop_re,   self.fmt_prop,    start, group=1)
        self._apply_re(text, self._var_re,    self.fmt_var,     start)
        self._apply_re(text, self._pseudo_re, self.fmt_pseudo,  start)
        self._apply_re(text, self._hex_re,    self.fmt_color,   start)
        self._apply_re(text, self._num_re,    self.fmt_num,     start)
        self._apply_re(text, self._str_re,    self.fmt_str,     start)
        self._apply_re(text, self._brace_re,  self.fmt_bracket, start)

        i = start
        while i < n:
            if text[i:i+2] == "/*":
                end = text.find("*/", i + 2)
                if end == -1:
                    self.setFormat(i, n - i, self.fmt_comment)
                    self.setCurrentBlockState(self.IN_COMMENT)
                    return
                self.setFormat(i, end + 2 - i, self.fmt_comment)
                i = end + 2
            else:
                i += 1

    def _apply_re(self, text, pattern, fmt, start=0, group=0):
        it = pattern.globalMatch(text, start)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)
