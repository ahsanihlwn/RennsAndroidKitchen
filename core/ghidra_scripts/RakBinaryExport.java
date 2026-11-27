// SPDX-License-Identifier: MPL-2.0
// RAK-authored adapter using Ghidra's public API; no Ghidra source is included.
// @category RAK
import ghidra.app.util.headless.HeadlessScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.block.*;
import com.google.gson.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class RakBinaryExport extends HeadlessScript {
    private String addr(Address a) { return a.toString(); }
    private JsonObject function(Function f) {
        JsonObject row = new JsonObject();
        row.addProperty("address", addr(f.getEntryPoint()));
        row.addProperty("name", f.getName());
        row.addProperty("signature", f.getSignature().toString());
        row.addProperty("size", f.getBody().getNumAddresses());
        row.addProperty("external", f.isExternal());
        return row;
    }
    private String stringValueAt(Address target) {
        Data data = currentProgram.getListing().getDefinedDataAt(target);
        if (data == null || !data.hasStringValue()) return "";
        Object val = data.getValue();
        return val == null ? "" : val.toString();
    }
    @Override public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2) throw new IllegalArgumentException("Expected output path and index/address");
        JsonObject result = new JsonObject(); result.addProperty("schema_version", 1);
        try {
            if (analysisTimeoutOccurred()) throw new IllegalStateException("Auto-analysis timed out; results were not published");
            result.addProperty("language", currentProgram.getLanguageID().toString());
            result.addProperty("image_base", addr(currentProgram.getImageBase()));
            FunctionManager manager = currentProgram.getFunctionManager();
            if (args[1].equals("index")) {
                JsonArray rows = new JsonArray();
                FunctionIterator it = manager.getFunctions(true);
                while (it.hasNext()) {
                    monitor.checkCancelled();
                    if (rows.size() == 100000) { result.addProperty("functions_truncated", true); break; }
                    rows.add(function(it.next()));
                }
                result.add("functions", rows);
                JsonArray stringXrefs = new JsonArray();
                ReferenceManager refMgr = currentProgram.getReferenceManager();
                DataIterator dataIt = currentProgram.getListing().getDefinedData(true);
                while (dataIt.hasNext()) {
                    monitor.checkCancelled();
                    Data data = dataIt.next();
                    if (!data.hasStringValue()) continue;
                    Object val = data.getValue();
                    if (val == null) continue;
                    String text = val.toString();
                    if (text.length() < 4) continue;
                    ReferenceIterator refsTo = refMgr.getReferencesTo(data.getAddress());
                    JsonArray refRows = new JsonArray();
                    while (refsTo.hasNext() && refRows.size() < 64) {
                        Reference ref = refsTo.next();
                        JsonObject r = new JsonObject();
                        r.addProperty("from", addr(ref.getFromAddress()));
                        Function fn = manager.getFunctionContaining(ref.getFromAddress());
                        r.addProperty("function", fn != null ? fn.getName() : "");
                        r.addProperty("function_address", fn != null ? addr(fn.getEntryPoint()) : "");
                        refRows.add(r);
                    }
                    if (refRows.size() > 0) {
                        if (stringXrefs.size() == 50000) { result.addProperty("string_xrefs_truncated", true); break; }
                        JsonObject entry = new JsonObject();
                        entry.addProperty("address", addr(data.getAddress()));
                        entry.addProperty("value", text);
                        entry.add("refs", refRows);
                        stringXrefs.add(entry);
                    }
                }
                result.add("string_xrefs", stringXrefs);
            } else {
                Address address = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(args[1]);
                Function f = manager.getFunctionContaining(address);
                if (f == null) f = manager.getFunctionAt(address);
                if (f == null) throw new IllegalArgumentException("No function at address " + args[1]);
                result.add("function", function(f));
                DecompInterface decompiler = new DecompInterface();
                try {
                    if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException(decompiler.getLastMessage());
                    DecompileResults decompiled = decompiler.decompileFunction(f, 60, monitor);
                    if (decompiled.decompileCompleted() && decompiled.getDecompiledFunction() != null)
                        result.addProperty("pseudocode", decompiled.getDecompiledFunction().getC());
                    else result.addProperty("decompile_error", decompiled.getErrorMessage());
                } finally { decompiler.dispose(); }
                JsonArray callers = new JsonArray(), callees = new JsonArray();
                for (Function caller : f.getCallingFunctions(monitor)) callers.add(function(caller));
                for (Function callee : f.getCalledFunctions(monitor)) callees.add(function(callee));
                result.add("callers", callers); result.add("callees", callees);
                JsonArray instructions = new JsonArray(), refs = new JsonArray();
                InstructionIterator listing = currentProgram.getListing().getInstructions(f.getBody(), true);
                while (listing.hasNext()) {
                    monitor.checkCancelled();
                    if (instructions.size() == 4096) { result.addProperty("listing_truncated", true); break; }
                    Instruction instruction = listing.next(); JsonObject row = new JsonObject();
                    row.addProperty("address", addr(instruction.getAddress())); row.addProperty("text", instruction.toString()); instructions.add(row);
                    for (Reference ref : instruction.getReferencesFrom()) {
                        if (refs.size() >= 20000) { result.addProperty("references_truncated", true); break; }
                        JsonObject reference = new JsonObject(); reference.addProperty("from", addr(ref.getFromAddress()));
                        reference.addProperty("to", addr(ref.getToAddress())); reference.addProperty("type", ref.getReferenceType().toString());
                        Symbol sym = currentProgram.getSymbolTable().getPrimarySymbol(ref.getToAddress());
                        if (sym != null) reference.addProperty("to_name", sym.getName());
                        String strVal = stringValueAt(ref.getToAddress());
                        if (!strVal.isEmpty()) reference.addProperty("string_value", strVal);
                        refs.add(reference);
                    }
                }
                result.add("instructions", instructions); result.add("references", refs);
                JsonArray blocks = new JsonArray();
                CodeBlockIterator iterator = new BasicBlockModel(currentProgram).getCodeBlocksContaining(f.getBody(), monitor);
                while (iterator.hasNext()) {
                    if (blocks.size() == 10000) { result.addProperty("blocks_truncated", true); break; }
                    CodeBlock block = iterator.next(); JsonObject row = new JsonObject(); row.addProperty("address", addr(block.getFirstStartAddress()));
                    JsonArray destinations = new JsonArray(); CodeBlockReferenceIterator edges = block.getDestinations(monitor);
                    while (edges.hasNext() && destinations.size() < 1024) destinations.add(addr(edges.next().getDestinationAddress()));
                    row.add("destinations", destinations); blocks.add(row);
                }
                result.add("blocks", blocks);
            }
        } catch (Exception e) {
            result.addProperty("error", e.toString()); printerr(e.toString());
        }
        Files.writeString(Path.of(args[0]), new GsonBuilder().setPrettyPrinting().create().toJson(result), StandardCharsets.UTF_8);
    }
}
