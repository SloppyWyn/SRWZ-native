// IOP IRX helper: names import stubs (<lib>_<ordinal>) from the IRX import tables
// (magic 0x41E00000), names exports from the export table (0x41C00000) as
// <lib>_export_<ordinal>, creates functions for the stubs' callers, then prints every
// function's decompiled C. Run after import + analysis:
//   analyzeHeadless <project dir> <project> -import local/code/iop/multi_streamer.irx
//       -processor MIPS:LE:32:default -scriptPath tools/ghidra -postScript IrxDecompile.java <out.txt>
// Output lines go to the file given as the script argument (plain text, no Ghidra chatter).
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.SourceType;

import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;

public class IrxDecompile extends GhidraScript {
    private PrintWriter out;

    private int rd32(Address a) throws Exception {
        return currentProgram.getMemory().getInt(a);
    }

    private String rdName(Address a) throws Exception {
        byte[] b = new byte[8];
        currentProgram.getMemory().getBytes(a, b);
        StringBuilder s = new StringBuilder();
        for (byte c : b) { if (c == 0) break; s.append((char) c); }
        return s.toString();
    }

    private void nameAt(Address a, String name) throws Exception {
        Function f = getFunctionAt(a);
        if (f == null) f = createFunction(a, name);
        if (f != null) f.setName(name, SourceType.USER_DEFINED);
        else createLabel(a, name, true);
    }

    private void scanTables() throws Exception {
        for (MemoryBlock blk : currentProgram.getMemory().getBlocks()) {
            if (!blk.isInitialized()) continue;
            Address a = blk.getStart();
            Address end = blk.getEnd();
            while (a.compareTo(end) < 0 && a.add(16).compareTo(end) <= 0) {
                int magic = rd32(a);
                if (magic == 0x41E00000 && rd32(a.add(4)) == 0) {
                    String lib = rdName(a.add(12));
                    out.println("# import table " + lib + " @ " + a);
                    Address p = a.add(20);
                    while (p.add(8).compareTo(end) <= 0 && rd32(p) == 0x03E00008) {
                        int li = rd32(p.add(4));
                        int ord = li & 0xFFFF;
                        nameAt(p, lib + "_" + ord);
                        p = p.add(8);
                    }
                    a = p;
                    continue;
                }
                if (magic == 0x41C00000 && rd32(a.add(4)) == 0) {
                    String lib = rdName(a.add(12));
                    out.println("# export table " + lib + " @ " + a);
                    Address p = a.add(20);
                    int ord = 0;
                    while (p.add(4).compareTo(end) <= 0) {
                        int v = rd32(p);
                        if (v == 0) break;
                        try {
                            Address t = toAddr(v & 0xFFFFFFFFL);
                            if (currentProgram.getMemory().contains(t)) {
                                nameAt(t, lib + "_export_" + ord);
                                out.println("#   export " + ord + " -> " + t);
                            }
                        } catch (Exception e) { }
                        ord++;
                        p = p.add(4);
                    }
                    a = p;
                    continue;
                }
                a = a.add(4);
            }
        }
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        out = new PrintWriter(args[0], "UTF-8");
        try {
            scanTables();
            DecompInterface decomp = new DecompInterface();
            decomp.openProgram(currentProgram);
            List<Function> fns = new ArrayList<>();
            FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
            while (it.hasNext()) fns.add(it.next());
            for (Function f : fns) {
                if (f.getName().matches("[a-z_0-9A-Z]+_[0-9]+") && f.getBody().getNumAddresses() <= 8) continue;
                StringBuilder callers = new StringBuilder();
                for (Reference r : getReferencesTo(f.getEntryPoint())) {
                    if (r.getReferenceType().isCall()) {
                        Function c = getFunctionContaining(r.getFromAddress());
                        callers.append(' ').append(r.getFromAddress()).append(c != null ? "(" + c.getName() + ")" : "");
                    }
                }
                DecompileResults res = decomp.decompileFunction(f, 60, monitor);
                out.println("== " + f.getName() + " @ " + f.getEntryPoint() + "\n   callers:" + callers);
                out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC()
                                                     : "(decompile failed: " + res.getErrorMessage() + ")");
            }
            decomp.dispose();
        } finally {
            out.close();
        }
    }
}
