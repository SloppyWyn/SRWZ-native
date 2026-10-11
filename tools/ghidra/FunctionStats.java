// Prints how many functions Ghidra found and how much of the code they cover.
// analyzeHeadless local/ghidra srwz -process SLPS_258.87 -noanalysis -scriptPath tools/ghidra -postScript FunctionStats.java
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class FunctionStats extends GhidraScript {
    @Override
    protected void run() throws Exception {
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        long count = 0, bytes = 0, thunks = 0, named = 0;
        long lo = Long.MAX_VALUE, hi = 0;
        while (it.hasNext()) {
            Function f = it.next();
            count++;
            if (f.isThunk()) thunks++;
            if (!f.getName().startsWith("FUN_")) named++;
            bytes += f.getBody().getNumAddresses();
            lo = Math.min(lo, f.getEntryPoint().getOffset());
            hi = Math.max(hi, f.getBody().getMaxAddress().getOffset());
        }
        println(String.format("functions=%d thunks=%d named=%d bytes=%d range=%#x-%#x instructions=%d",
            count, thunks, named, bytes, lo, hi, currentProgram.getListing().getNumInstructions()));
    }
}
