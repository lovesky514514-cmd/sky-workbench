package workbench.bridge;

import static workbench.bridge.ComsolBridgeServer.array;
import static workbench.bridge.ComsolBridgeServer.field;
import static workbench.bridge.ComsolBridgeServer.invoke;

import java.io.BufferedOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class ComsolVisualization {
    private static final Class<?>[] NONE = {};
    private static final Class<?>[] TAG = {String.class};
    private static final int MAX_VERTICES = 250_000;
    private static final int MAX_ELEMENTS = 500_000;
    private static final long MAX_BYTES = 32_000_000;
    private static final List<String> TYPES = List.of("vtx", "edg", "tri", "quad", "tet", "pyr", "prism", "hex");
    private static final int[] CORNERS = {1, 2, 3, 4, 4, 5, 6, 8};

    private static final class Geometry {
        final Object component, geometry;
        final String componentTag, tag;
        final int dimension;
        Geometry(Object component, Object geometry, String componentTag, String tag, int dimension) {
            this.component = component; this.geometry = geometry;
            this.componentTag = componentTag; this.tag = tag; this.dimension = dimension;
        }
    }

    private ComsolVisualization() {}

    public static String publish(Object model, String version, Path directory) throws Exception {
        return publish(model, version, directory, false);
    }

    public static String publish(Object model, String version, Path directory, boolean heatResult) throws Exception {
        if (model == null) throw new IllegalStateException("Model not connected");
        Geometry scope = builtGeometry(model);
        Object geom = scope.geometry;
        int dimension = scope.dimension;
        String unit = (String) invoke(geom, "lengthUnit", NONE);
        double[] bounds = (double[]) invoke(geom, "getBoundingBox", NONE);
        List<String> objects = List.of((String[]) invoke(geom, "objectNames", NONE));
        String meshTag = "";
        Object mesh = null;
        for (String tag : tags(scope.component, "mesh")) {
            Object candidate = invoke(scope.component, "mesh", TAG, tag);
            if (("geom/" + scope.tag).equals(invoke(candidate, "geometricModel", NONE))
                    && !Boolean.TRUE.equals(invoke(candidate, "isEmpty", NONE))
                    && Boolean.TRUE.equals(invoke(candidate, "isComplete", NONE))
                    && currentMesh(candidate)) {
                meshTag = tag;
                mesh = candidate;
                break;
            }
        }
        int[] counts = {(int) invoke(geom, "getNVertices", NONE),
            (int) invoke(geom, dimension == 2 ? "getNBoundaries" : "getNEdges", NONE),
            (int) invoke(geom, dimension == 2 ? "getNDomains" : "getNBoundaries", NONE),
            dimension == 3 ? (int) invoke(geom, "getNDomains", NONE) : 0};
        int boundaryCount = counts[dimension - 1];
        boolean mapped = mesh == null && dimension == 3 && objects.size() == 1 &&
            boundaryCount <= 128 && matchingFaces(geom, objects.get(0), boundaryCount);
        String encoding = dimension == 2 && mesh == null ? "boundary_curves" : mapped ? "boundary_triangles" : "object_triangles";
        HeatSamples field = heatResult ? temperature(model, scope, mesh) : null;
        Matcher versionNumber = Pattern.compile("[0-9]+\\.[0-9]+(?:\\.[0-9]+)*").matcher(version);
        if (!versionNumber.find()) throw new IOException("Unknown COMSOL version");
        String meta = "{" + field("component_tag", scope.componentTag) + "," + field("geometry_tag", scope.tag) + "," +
            field("mesh_tag", meshTag) + "," + field("comsol_version", versionNumber.group()) + "," + field("length_unit", unit) +
            ",\"dimension\":" + dimension + ",\"bounds\":" + Arrays.toString(bounds) + ",\"entity_counts\":" +
            Arrays.toString(counts) + "," + array("geometry_objects", objects) + "," + field("geometry_encoding", encoding) +
            ",\"boundary_features\":[" + String.join(",", assignments(scope.component, "physics", dimension - 1)) +
            "],\"domain_materials\":[" + String.join(",", assignments(scope.component, "material", dimension)) +
            "],\"boundary_domains\":{" + adjacency(geom, dimension, counts[dimension - 1]) + "}" +
            (field == null ? "" : ",\"heat_field\":{" + field("dataset", field.dataset) + ",\"time_s\":" + field.time + "}") + "}";
        byte[] metadata = meta.getBytes(StandardCharsets.UTF_8);
        if (metadata.length > 262_144) throw new IOException("Visualization metadata exceeds its limit");
        Path temporary = Files.createTempFile(directory, ".view-", ".tmp");
        try {
            try (DataOutputStream output = new DataOutputStream(new BufferedOutputStream(Files.newOutputStream(temporary)))) {
                output.writeInt(field == null ? 0x53575631 : 0x53575632);
                output.writeInt(metadata.length);
                output.write(metadata);
                if (mesh != null) writeMesh(output, mesh, dimension);
                else if (dimension == 2) writeCurves(output, geom, boundaryCount);
                else writeGeometry(output, geom, directory, objects, mapped ? boundaryCount : 0);
                if (field != null) {
                    output.writeInt(field.values.length);
                    for (double value : field.values) output.writeDouble(value);
                }
            }
            try (FileChannel channel = FileChannel.open(temporary, StandardOpenOption.WRITE)) { channel.force(true); }
            long bytes = Files.size(temporary);
            if (bytes > MAX_BYTES || !directory.toRealPath().equals(directory)) throw new IOException("Visualization limit");
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (var input = Files.newInputStream(temporary)) {
                byte[] block = new byte[8192];
                for (int n; (n = input.read(block)) != -1;) digest.update(block, 0, n);
            }
            Files.createLink(directory.resolve("topology.bin"), temporary);
            StringBuilder hash = new StringBuilder(64);
            for (byte value : digest.digest()) hash.append(String.format("%02x", value & 0xff));
            return "\"bytes\":" + bytes + "," + field("sha256", hash.toString());
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    private static Geometry builtGeometry(Object model) throws Exception {
        Geometry planar = null;
        for (String ctag : tags(model, "component")) {
            Object component = invoke(model, "component", TAG, ctag);
            for (String tag : tags(component, "geom")) {
                Object geom = invoke(component, "geom", TAG, tag);
                if (!tags(geom, "feature").contains("fin")) continue;
                Object fin = invoke(geom, "feature", TAG, "fin");
                if (!Boolean.TRUE.equals(invoke(fin, "isBuilt", NONE))) continue;
                int dimension = (int) invoke(geom, "getSDim", NONE);
                if (dimension == 3) return new Geometry(component, geom, ctag, tag, dimension);
                if (dimension == 2 && planar == null) planar = new Geometry(component, geom, ctag, tag, dimension);
            }
        }
        if (planar == null) throw new IllegalStateException("No finalized geometry");
        return planar;
    }

    private static boolean currentMesh(Object mesh) throws Exception {
        for (String tag : tags(mesh, "feature")) {
            Object feature = invoke(mesh, "feature", TAG, tag);
            if (!Boolean.TRUE.equals(invoke(feature, "isBuilt", NONE))) return false;
        }
        return true;
    }

    private static void writeMesh(DataOutputStream output, Object mesh, int dimension) throws Exception {
        int vertices = (int) invoke(mesh, "getNumVertex", NONE);
        if (vertices <= 0 || vertices > MAX_VERTICES) throw new IOException("Mesh vertex limit");
        double[][] xyz = (double[][]) invoke(mesh, "getVertex", NONE);
        if (xyz.length != dimension || Arrays.stream(xyz).anyMatch(axis -> axis.length != vertices))
            throw new IOException("Invalid mesh coordinate shape");
        output.writeInt(vertices);
        for (int i = 0; i < vertices; i++)
            for (double[] axis : xyz) output.writeDouble(axis[i]);
        String[] types = (String[]) invoke(mesh, "getTypes", NONE);
        output.writeInt(types.length);
        long total = 0;
        for (String type : types) {
            int code = TYPES.indexOf(type);
            if (code < 0) throw new IOException("Unsupported COMSOL element type");
            int count = (int) invoke(mesh, "getNumElem", TAG, type);
            total += count;
            if (count <= 0 || total > MAX_ELEMENTS) throw new IOException("Mesh element limit");
            int[][] corners = (int[][]) invoke(mesh, "getElem", TAG, type);
            int[] entities = (int[]) invoke(mesh, "getElemEntity", TAG, type);
            if (corners.length != CORNERS[code] || entities.length != count ||
                    Arrays.stream(corners).anyMatch(row -> row.length != count)) throw new IOException("Invalid mesh topology shape");
            output.writeInt(code);
            output.writeInt(count);
            for (int i = 0; i < count; i++) {
                output.writeInt(entities[i]);
                for (int[] corner : corners) output.writeInt(corner[i]);
            }
        }
        output.writeInt(0);
    }

    private static final class HeatSamples {
        final String dataset;
        final double time;
        final double[] values;
        HeatSamples(String dataset, double time, double[] values) {
            this.dataset = dataset;
            this.time = time;
            this.values = values;
        }
    }

    private static HeatSamples temperature(Object model, Geometry geometry, Object mesh) throws Exception {
        if (geometry.dimension != 3 || mesh == null || tags(model, "component").size() != 1 ||
            tags(geometry.component, "geom").size() != 1 || tags(geometry.component, "mesh").size() != 1)
            throw new IOException("Heat field requires one 3D mesh");
        Object result = invoke(model, "result", NONE);
        List<String> datasets = tags(result, "dataset");
        if (datasets.size() != 1) throw new IOException("Heat solution dataset is ambiguous");
        String datasetTag = datasets.get(0);
        Object dataset = invoke(result, "dataset", TAG, datasetTag);
        String solutionTag = (String) invoke(dataset, "getString", TAG, "solution");
        Object solution = invoke(model, "sol", TAG, solutionTag);
        if (!Arrays.equals((String[]) invoke(solution, "getPNames", NONE), new String[]{"t"}))
            throw new IOException("Heat time axis is ambiguous");
        double[] times = (double[]) invoke(solution, "getPVals", NONE);
        if (times.length == 0 || !Double.isFinite(times[times.length - 1]) || times[times.length - 1] < 0)
            throw new IOException("No saved Heat solution time");
        int nodes = (int) invoke(mesh, "getNumVertex", NONE);
        if (nodes <= 0 || nodes > MAX_VERTICES) throw new IOException("Heat vertex limit");
        double[][] xyz = (double[][]) invoke(mesh, "getVertex", NONE);
        Object numericals = invoke(result, "numerical", NONE);
        String tag = "skyView" + java.util.UUID.randomUUID().toString().replace("-", "");
        Object interp = invoke(numericals, "create", new Class<?>[]{String.class, String.class}, tag, "Interp");
        try {
            Class<?>[] pair = {String.class, String.class};
            invoke(interp, "set", pair, "data", datasetTag);
            invoke(interp, "set", pair, "expr", "T");
            invoke(interp, "set", pair, "unit", "K");
            invoke(interp, "set", new Class<?>[]{String.class, double.class}, "ext", 0.0);
            invoke(interp, "set", new Class<?>[]{String.class, int[].class}, "solnum", new int[]{times.length});
            invoke(interp, "setInterpolationCoordinates", new Class<?>[]{double[][].class}, (Object) xyz);
            double[][] samples = (double[][]) invoke(interp, "getData", new Class<?>[]{int.class}, 0);
            if (Boolean.TRUE.equals(invoke(interp, "isComplex", NONE)) || samples.length != 1 || samples[0].length != nodes ||
                !Arrays.deepEquals(xyz, (double[][]) invoke(interp, "getCoordinates", NONE)) ||
                !Arrays.stream(samples[0]).allMatch(value -> Double.isFinite(value) && value > 0 && value < 1e7))
                throw new IOException("Heat coordinates, dimensions or values do not match topology");
            return new HeatSamples(datasetTag, times[times.length - 1], samples[0]);
        } finally {
            invoke(numericals, "remove", TAG, tag);
        }
    }

    private static void writeCurves(DataOutputStream output, Object geom, int count) throws Exception {
        if (count <= 0 || count > 1024) throw new IOException("Planar boundary limit");
        output.writeInt(0);
        output.writeInt(0);
        output.writeInt(count);
        for (int i = 1; i <= count; i++) {
            double[] bounds = (double[]) invoke(geom, "edgeParamRange", new Class<?>[]{int.class}, i);
            double[] samples = new double[65];
            for (int j = 0; j < samples.length; j++) samples[j] = bounds[0] + (bounds[1] - bounds[0]) * j / 64;
            double[][] xy = (double[][]) invoke(geom, "edgeX", new Class<?>[]{int.class, double[].class}, i, samples);
            if (xy.length != samples.length) throw new IOException("Invalid planar curve");
            output.writeInt(xy.length);
            for (double[] point : xy) {
                if (point.length != 2) throw new IOException("Invalid planar coordinate");
                output.writeDouble(point[0]);
                output.writeDouble(point[1]);
            }
        }
    }

    private static boolean matchingFaces(Object geom, String name, int count) {
        // Object-local face numbers can differ from finalized ones. Admit a
        // single-object mapping only after comparing each numbered face.
        try {
            if (Boolean.TRUE.equals(invoke(geom, "isAssembly", NONE)) ||
                Boolean.TRUE.equals(invoke(geom, "isVirtual", NONE))) return false;
            Object object = invoke(geom, "obj", TAG, name);
            if ((int) invoke(object, "getNFaces", NONE) != count || count == 0) return false;
            Class<?>[] id = {int.class};
            Class<?>[] parameters = {int.class, double[][].class};
            for (int i = 1; i <= count; i++) {
                double[] a = (double[]) invoke(geom, "faceParamRange", id, i);
                double[] b = (double[]) invoke(object, "faceParamRange", id, i);
                if (a.length != 4 || !Arrays.equals(a, b)) return false;
                double[][] points = new double[9][2];
                for (int j = 0; j < 9; j++) {
                    points[j][0] = a[0] + (a[1] - a[0]) * (j % 3 + 1) / 4;
                    points[j][1] = a[2] + (a[3] - a[2]) * (j / 3 + 1) / 4;
                }
                for (String member : List.of("faceX", "faceNormal")) {
                    double[][] finalValues = (double[][]) invoke(geom, member, parameters, i, points);
                    double[][] objectValues = (double[][]) invoke(object, member, parameters, i, points);
                    if (!Arrays.deepEquals(finalValues, objectValues) || !Arrays.stream(finalValues).flatMapToDouble(Arrays::stream).allMatch(Double::isFinite))
                        return false;
                }
                int[] finalEdges = (int[]) invoke(geom, "getAdj", new Class<?>[]{int.class, int.class, int.class}, 2, 1, i);
                int[] objectEdges = (int[]) invoke(object, "getAdj", new Class<?>[]{int.class, int.class, int.class}, 2, 1, i);
                if (!Arrays.equals(finalEdges, objectEdges)) return false;
            }
            return true;
        } catch (ReflectiveOperationException | RuntimeException ex) {
            return false;
        }
    }

    private static void writeGeometry(DataOutputStream output, Object geom, Path directory,
                                      List<String> objects, int boundaries) throws Exception {
        output.writeInt(0); // No finite-element mesh data.
        output.writeInt(0);
        if (objects.isEmpty() || objects.size() > 64)
            throw new IOException("Unsupported geometry-only representation");
        output.writeInt(boundaries > 0 ? boundaries : objects.size());
        Object export = invoke(geom, "export", NONE);
        Object selection = invoke(export, "selection", NONE);
        int total = 0;
        for (int iObject = 0; iObject < (boundaries > 0 ? boundaries : objects.size()); iObject++) {
            Path file = Files.createTempFile(directory, ".surface-", ".stl");
            try {
                invoke(export, "setSTLFormat", TAG, "binary");
                if (boundaries > 0) {
                    invoke(selection, "init", new Class<?>[]{int.class}, 2);
                    invoke(selection, "set", new Class<?>[]{String.class, int[].class}, objects.get(0), new int[]{iObject + 1});
                } else {
                    invoke(selection, "init", NONE);
                    invoke(selection, "set", new Class<?>[]{String[].class}, (Object) new String[]{objects.get(iObject)});
                }
                invoke(geom, "export", TAG, file.toString());
                long size = Files.size(file);
                if (size < 84 || size > 50L * 80_000 + 84) throw new IOException("STL surface limit");
                ByteBuffer stl = ByteBuffer.wrap(Files.readAllBytes(file)).order(ByteOrder.LITTLE_ENDIAN);
                int count = stl.getInt(80);
                total += count;
                if (count <= 0 || total > 80_000 || size != 84L + 50L * count) throw new IOException("Invalid STL surface");
                output.writeInt(count);
                for (int i = 0; i < count; i++)
                    for (int coordinate = 0; coordinate < 9; coordinate++)
                        output.writeFloat(stl.getFloat(96 + 50 * i + 4 * coordinate));
            } finally {
                Files.deleteIfExists(file);
            }
        }
    }

    private static String adjacency(Object geom, int dimension, int count) throws Exception {
        if (count < 0 || count > 8_000) throw new IOException("Boundary metadata limit");
        int[][] edges = (int[][]) invoke(geom, "getAdj", new Class<?>[]{int.class, int.class}, dimension - 1, dimension);
        if (edges.length != count + 1) throw new IOException("Unexpected boundary adjacency");
        List<String> links = new ArrayList<>();
        for (int i = 1; i <= count; i++) links.add("\"" + i + "\":" + Arrays.toString(edges[i]));
        return String.join(",", links);
    }

    private static List<String> assignments(Object component, String kind, int dimension) throws Exception {
        List<String> entries = new ArrayList<>();
        for (String tag : tags(component, kind)) {
            Object node = invoke(component, kind, TAG, tag);
            if (kind.equals("physics")) {
                for (String feature : tags(node, "feature"))
                    addAssignment(entries, invoke(node, "feature", TAG, feature), tag + "/" + feature, dimension);
            } else addAssignment(entries, node, tag, dimension);
        }
        if (entries.size() > 256) throw new IOException("Selection metadata limit");
        return entries;
    }

    private static void addAssignment(List<String> entries, Object node, String tag, int dimension) throws Exception {
        try {
            if (!Boolean.TRUE.equals(invoke(node, "isActive", NONE))) return;
            Object selection = invoke(node, "selection", NONE);
            int[] ids = (int[]) invoke(selection, "entities", new Class<?>[]{int.class}, dimension);
            if (ids.length == 0 || ids.length > 8_000) return;
            String label = String.valueOf(invoke(node, "label", NONE)).replaceAll("[\\p{Cntrl}]", " ");
            if (label.matches(".*(?:[A-Za-z]:[\\\\/]|\\\\\\\\|/home/|/Users/).*")) label = tag;
            if (label.length() > 160) label = label.substring(0, 160);
            entries.add("{" + field("tag", tag) + "," + field("label", label) + ",\"ids\":" + Arrays.toString(ids) + "}");
        } catch (ReflectiveOperationException | RuntimeException ignored) {
            // A feature with an inaccessible selection contributes no assignment.
        }
    }

    private static List<String> tags(Object node, String list) throws Exception {
        return List.of((String[]) invoke(invoke(node, list, NONE), "tags", NONE));
    }
}
