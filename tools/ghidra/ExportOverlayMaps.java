// Runs PS2Recomp's ExportPS2Functions.java for the current program with output paths
// derived from its name, so a headless run over a whole project folder exports every
// overlay's function map: <outdir>/<name>/<name>.toml and <name>_functions.csv.
// analyzeHeadless local/ghidra srwz/overlays -process -noanalysis -readOnly
//     -scriptPath "tools/ghidra;third_party/PS2Recomp/ps2xRecomp/tools/ghidra"
//     -postScript ExportOverlayMaps.java local/recomp/overlays
import ghidra.app.script.GhidraScript;

import java.io.File;

public class ExportOverlayMaps extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            printerr("usage: ExportOverlayMaps.java <output dir>");
            return;
        }
        String name = currentProgram.getName().replaceAll("\\.elf$", "");
        File dir = new File(args[0], name);
        if (!dir.isDirectory() && !dir.mkdirs()) {
            printerr("cannot create " + dir);
            return;
        }
        runScript("ExportPS2Functions.java", new String[] {
            new File(dir, name + ".toml").getAbsolutePath(),
            new File(dir, name + "_functions.csv").getAbsolutePath(),
        });
    }
}
