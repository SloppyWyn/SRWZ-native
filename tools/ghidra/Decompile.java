// Prints Ghidra's decompiled C for the functions containing the given addresses, with
// their callers. Arguments: one or more hex addresses.
// analyzeHeadless local/ghidra srwz -process SLPS_258.87 -noanalysis -readOnly
//     -scriptPath tools/ghidra -postScript Decompile.java 0x11e50c 0x11e8d0
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

import java.util.LinkedHashSet;
import java.util.Set;

public class Decompile extends GhidraScript {
    @Override
    protected void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        Set<Function> seen = new LinkedHashSet<>();
        for (String arg : getScriptArgs()) {
            Address at = toAddr(Long.decode(arg));
            Function f = getFunctionContaining(at);
            if (f == null) {
                println("== " + arg + ": not inside a function");
                continue;
            }
            if (!seen.add(f)) continue;
            StringBuilder callers = new StringBuilder();
            for (Reference r : getReferencesTo(f.getEntryPoint())) {
                if (r.getReferenceType().isCall()) {
                    Function c = getFunctionContaining(r.getFromAddress());
                    callers.append(' ').append(r.getFromAddress())
                           .append(c != null ? "(" + c.getName() + ")" : "");
                }
            }
            DecompileResults res = decomp.decompileFunction(f, 60, monitor);
            println("== " + arg + " in " + f.getName() + " @ " + f.getEntryPoint()
                    + "\n   callers:" + callers + "\n"
                    + (res.decompileCompleted() ? res.getDecompiledFunction().getC() : "(decompile failed: " + res.getErrorMessage() + ")"));
        }
        decomp.dispose();
    }
}
