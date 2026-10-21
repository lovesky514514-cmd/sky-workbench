package workbench.bridge;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.lang.reflect.Array;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Minimal localhost bridge. Scientific planning and validation stay in Python. */
public final class ComsolBridgeServer {
    private static final int MAX_EVENTS = 500;
    private static final AtomicLong SEQUENCE = new AtomicLong(0);
    private static final Deque<String> EVENTS = new ArrayDeque<>();
    private static final Pattern JSON_STRING = Pattern.compile("\\\"([A-Za-z0-9_]+)\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    private static final Pattern JSON_NUMBER = Pattern.compile("\\\"([A-Za-z0-9_]+)\\\"\\s*:\\s*(-?[0-9]+(?:\\.[0-9]+)?)");
    private static final Pattern SAFE_TAG = Pattern.compile("[A-Za-z][A-Za-z0-9_]{0,63}");
    private static final Pattern LITERAL_KELVIN = Pattern.compile("\\s*([0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\\s*\\[K\\]\\s*");
    private static final Pattern PROGRESS_LINE = Pattern.compile("(?:Current progress|当前进度):\\s*(\\d+)\\s*%\\s*-?\\s*(.*)");
    private static final Pattern BRIDGE_TOKEN_FORMAT = Pattern.compile("[0-9a-f]{64}");
    private static final String BRIDGE_TOKEN_HEADER = "X-Workbench-Bridge-Token";
    private static final String BRIDGE_TOKEN_ENV = "COMSOL_WORKBENCH_JAVA_BRIDGE_TOKEN";
    private static final RuntimeState RUNTIME = new RuntimeState();
    private static volatile long LAST_STUDY_DATE = 0L;
    private static byte[] bridgeToken;

    private ComsolBridgeServer() {}

    public static void main(String[] args) throws Exception {
        // Fail before loading a model if the owning Runtime did not provide a credential.
        String startupToken = System.getenv(BRIDGE_TOKEN_ENV);
        if (startupToken == null || !BRIDGE_TOKEN_FORMAT.matcher(startupToken).matches()) {
            throw new IllegalStateException("Java Bridge authentication unavailable");
        }
        bridgeToken = startupToken.getBytes(StandardCharsets.US_ASCII);
        int port = argumentPort(args, 8765);
        RUNTIME.initialize(args);
        InetSocketAddress address = new InetSocketAddress(InetAddress.getLoopbackAddress(), port);
        HttpServer server = HttpServer.create(address, 0);
        server.createContext("/health", authenticated(ComsolBridgeServer::health));
        server.createContext("/model/tree", authenticated(ComsolBridgeServer::modelTree));
        server.createContext("/model/parameters", authenticated(ComsolBridgeServer::modelParameters));
        server.createContext("/model/parameter", authenticated(ComsolBridgeServer::modelParameter));
        server.createContext("/model/boundary-feature", authenticated(ComsolBridgeServer::boundaryFeature));
        server.createContext("/model/heat-core", authenticated(ComsolBridgeServer::heatCore));
        server.createContext("/model/study/run", authenticated(ComsolBridgeServer::runStudy));
        server.createContext("/model/heat-field", authenticated(ComsolBridgeServer::heatField));
        server.createContext("/model/heat-export", authenticated(ComsolBridgeServer::heatExport));
        server.createContext("/commands/test-feature", authenticated(ComsolBridgeServer::testFeature));
        server.createContext("/progress", authenticated(ComsolBridgeServer::progress));
        server.createContext("/events", authenticated(ComsolBridgeServer::events));
        server.setExecutor(Executors.newFixedThreadPool(4));
        appendEvent("command", "bridge", "ready", true, false, false, "Java Bridge listener started");
        server.start();
        System.out.println("COMSOL Java Bridge listening on http://127.0.0.1:" + port);
    }

    private static HttpHandler authenticated(HttpHandler handler) {
        // Check before dispatch, so even malformed requests cannot enter COMSOL handlers.
        return exchange -> {
            List<String> values = exchange.getRequestHeaders().get(BRIDGE_TOKEN_HEADER);
            String supplied = values != null && values.size() == 1 ? values.get(0) : null;
            if (supplied == null || !BRIDGE_TOKEN_FORMAT.matcher(supplied).matches()
                    || !MessageDigest.isEqual(bridgeToken, supplied.getBytes(StandardCharsets.US_ASCII))) {
                send(exchange, 401, "{" + field("detail", "Unauthorized") + "}");
                return;
            }
            handler.handle(exchange);
        };
    }

    private static void health(HttpExchange exchange) throws IOException {
        if (!method(exchange, "GET")) return;
        Probe probe = Probe.detect();
        String payload = "{" +
            field("bridge", "ok") + "," +
            field("status", probe.connected ? "ready" : "unavailable") + "," +
            bool("bridge_running", true) + "," +
            bool("comsol_api_loaded", probe.apiLoaded) + "," +
            bool("model_connected", probe.connected) + "," +
            bool("connected", probe.connected) + "," +
            bool("real_comsol", probe.connected) + "," +
            field("comsol_version", probe.comsolVersion) + "," +
            field("model_tag", probe.modelTag) + "," +
            field("bridge_mode", RUNTIME.mode) + "," +
            field("connection_mode", RUNTIME.mode) + "," +
            field("detection_method", RUNTIME.detectionMethod) + "," +
            number("poll_interval_ms", RUNTIME.pollMs) + "," +
            field("detail", probe.detail) + "," +
            array("model_tags", probe.modelTags) + "," +
            bool("command_success", true) + "," +
            bool("model_state_verified", probe.connected) + "," +
            bool("scientific_validation_passed", false) +
            "}";
        send(exchange, 200, payload);
    }

    private static void modelTree(HttpExchange exchange) throws IOException {
        if (!method(exchange, "GET")) return;
        Probe probe = Probe.detect();
        if (!probe.connected) {
            appendEvent("command", "model_tree", "unavailable", false, false, false, probe.detail);
            send(exchange, 503, failure("model_tree", probe.detail));
            return;
        }
        Map<String, String> query = query(exchange.getRequestURI());
        String modelTag = query.getOrDefault("model_tag", probe.modelTags.get(0));
        try {
            Object model = invokeStatic(probe.modelUtil, "model", new Class<?>[]{String.class}, modelTag);
            List<String> components = nestedTags(model, "component");
            List<String> geometries = new ArrayList<>();
            List<String> physics = new ArrayList<>();
            List<String> meshes = new ArrayList<>();
            for (String componentTag : components) {
                Object component = invoke(model, "component", new Class<?>[]{String.class}, componentTag);
                addPrefixed(geometries, componentTag, nestedTags(component, "geom"));
                addPrefixed(physics, componentTag, nestedTags(component, "physics"));
                addPrefixed(meshes, componentTag, nestedTags(component, "mesh"));
            }
            List<String> studies = nestedTags(model, "study");
            List<String> solvers = nestedTags(model, "sol");
            List<String> results = nestedTags(model, "result");
            Map<String, String> parameters = readParameters(model);
            String payload = "{" +
                field("status", "ready") + "," + field("model_tag", modelTag) + "," +
                array("components", components) + "," + array("geometry", geometries) + "," +
                array("physics", physics) + "," + array("mesh", meshes) + "," +
                array("studies", studies) + "," + array("solvers", solvers) + "," +
                array("results", results) + "," + number("parameter_count", parameters.size()) + "," +
                bool("command_success", true) + "," +
                bool("model_state_verified", true) + "," + bool("scientific_validation_passed", false) + "}";
            appendEvent("command", "model_tree", "verified", true, true, false, "Model tree read back from COMSOL",
                values("target", "model:" + modelTag, "action", "readback", "detection_method", "direct_model_object"));
            send(exchange, 200, payload);
        } catch (ReflectiveOperationException | RuntimeException exc) {
            appendEvent("critical_failure", "model_tree", "error", false, false, false, exc.toString());
            send(exchange, 500, failure("model_tree", exc.toString()));
        }
    }

    private static void modelParameters(HttpExchange exchange) throws IOException {
        if (!method(exchange, "GET")) return;
        Probe probe = Probe.detect();
        if (!probe.connected) {
            send(exchange, 503, failure("parameters", probe.detail));
            return;
        }
        try {
            Object model = invokeStatic(probe.modelUtil, "model", new Class<?>[]{String.class}, probe.modelTag);
            Map<String, String> parameters = readParameters(model);
            send(exchange, 200, "{" + field("status", "ready") + "," + field("model_tag", probe.modelTag) + "," +
                object("parameters", parameters) + "," + number("count", parameters.size()) + "," +
                bool("command_success", true) + "," + bool("model_state_verified", true) + "," +
                bool("scientific_validation_passed", false) + "}");
        } catch (ReflectiveOperationException | RuntimeException exc) {
            appendEvent("critical_failure", "parameters", "error", false, false, false, rootCause(exc));
            send(exchange, 500, failure("parameters", rootCause(exc)));
        }
    }

    private static void modelParameter(HttpExchange exchange) throws IOException {
        String requestMethod = exchange.getRequestMethod();
        if (!"POST".equalsIgnoreCase(requestMethod) && !"DELETE".equalsIgnoreCase(requestMethod)) {
            send(exchange, 405, "{" + field("status", "error") + "," + field("detail", "Method not allowed") + "}");
            return;
        }
        Probe probe = Probe.detect();
        if (!probe.connected) {
            send(exchange, 503, failure("set_parameter", probe.detail));
            return;
        }
        Map<String, String> values = "POST".equalsIgnoreCase(requestMethod)
            ? parseFlatJson(readBody(exchange)) : query(exchange.getRequestURI());
        String name = values.getOrDefault("name", "");
        String value = values.getOrDefault("value", "");
        if (!SAFE_TAG.matcher(name).matches() || ("POST".equalsIgnoreCase(requestMethod) && value.isBlank())) {
            send(exchange, 400, "{" + field("status", "error") + "," +
                field("detail", "A safe parameter name and non-empty value are required") + "," +
                bool("command_success", false) + "," + bool("model_state_verified", false) + "," +
                bool("scientific_validation_passed", false) + "}");
            return;
        }
        if ("DELETE".equalsIgnoreCase(requestMethod) && !name.startsWith("wb_probe_")) {
            send(exchange, 403, "{" + field("status", "error") + "," +
                field("detail", "Only scoped wb_probe_ parameters may be removed") + "," +
                bool("command_success", false) + "," + bool("model_state_verified", false) + "," +
                bool("scientific_validation_passed", false) + "}");
            return;
        }
        appendEvent("command", "parameter", "submitted", false, false, false,
            ("POST".equalsIgnoreCase(requestMethod) ? "Setting " : "Removing ") + name,
            values("action", "POST".equalsIgnoreCase(requestMethod) ? "set_parameter" : "remove_parameter", "target", "param:" + name));
        try {
            Object model = invokeStatic(probe.modelUtil, "model", new Class<?>[]{String.class}, probe.modelTag);
            Object parameters = invoke(model, "param", new Class<?>[]{});
            Map<String, String> before = readParameters(model);
            boolean existed = before.containsKey(name);
            String oldValue = before.getOrDefault(name, "");
            boolean verified;
            String readback;
            if ("POST".equalsIgnoreCase(requestMethod)) {
                invoke(parameters, "set", new Class<?>[]{String.class, String.class}, name, value);
                readback = String.valueOf(invoke(parameters, "get", new Class<?>[]{String.class}, name));
                verified = expressionsEqual(value, readback);
            } else {
                invoke(parameters, "remove", new Class<?>[]{String.class}, name);
                Map<String, String> after = readParameters(model);
                readback = after.getOrDefault(name, "");
                verified = !after.containsKey(name);
            }
            RUNTIME.replaceParameterSnapshot(readParameters(model));
            Map<String, Object> extra = new LinkedHashMap<>();
            extra.put("target", "param:" + name);
            extra.put("action", "POST".equalsIgnoreCase(requestMethod) ? (existed ? "updated" : "created") : "removed");
            extra.put("verified", verified);
            extra.put("old_value", oldValue);
            extra.put("new_value", readback);
            extra.put("requested", "POST".equalsIgnoreCase(requestMethod) ? value : "<removed>");
            extra.put("detection_method", "direct_readback");
            appendEvent("model_change", "parameter", verified ? "verified" : "unverified", true, verified, false,
                verified ? "COMSOL parameter readback matched" : "COMSOL parameter readback did not match", extra);
            String payload = "{" + field("status", verified ? "verified" : "unverified") + "," +
                field("model_tag", probe.modelTag) + "," + field("name", name) + "," +
                field("requested", "POST".equalsIgnoreCase(requestMethod) ? value : "<removed>") + "," +
                field("readback", readback) + "," + field("old_value", oldValue) + "," +
                bool("existed", existed) + "," + bool("command_success", true) + "," +
                bool("model_state_verified", verified) + "," + bool("scientific_validation_passed", false) + "}";
            send(exchange, verified ? 200 : 409, payload);
        } catch (ReflectiveOperationException | RuntimeException exc) {
            appendEvent("critical_failure", "parameter", "error", false, false, false, rootCause(exc),
                values("action", "set_parameter", "target", "param:" + name));
            send(exchange, 500, failure("set_parameter", rootCause(exc)));
        }
    }

    private static void runStudy(HttpExchange exchange) throws IOException {
        if (!method(exchange, "POST")) return;
        Probe probe = Probe.detect();
        if (!probe.connected) {
            send(exchange, 503, failure("run_study", probe.detail));
            return;
        }
        Map<String, String> values = parseFlatJson(readBody(exchange));
        String studyTag = values.getOrDefault("study_tag", "std1");
        if (!SAFE_TAG.matcher(studyTag).matches()) {
            send(exchange, 400, failure("run_study", "A safe study_tag is required"));
            return;
        }
        long started = System.nanoTime();
        appendEvent("command", "solve", "submitted", false, false, false, "Run study " + studyTag,
            values("action", "run_study", "target", "study:" + studyTag));
        appendEvent("progress", "solve", "started", true, false, false, "COMSOL study.run started",
            values("stage", "solve", "message", "Study execution entered COMSOL", "progress_source", "study.run"));
        try {
            Object model = invokeStatic(probe.modelUtil, "model", new Class<?>[]{String.class}, probe.modelTag);
            List<String> tags = nestedTags(model, "study");
            if (!tags.contains(studyTag)) {
                send(exchange, 404, failure("run_study", "Study tag not found in the real COMSOL model"));
                return;
            }
            Object study = invoke(model, "study", new Class<?>[]{String.class}, studyTag);
            invoke(study, "run", new Class<?>[]{});
            RUNTIME.pollProgressLog();
            long computationDate = ((Number) invoke(study, "getLastComputationDate", new Class<?>[]{})).longValue();
            boolean verified = computationDate > 0;
            if (verified) LAST_STUDY_DATE = computationDate;
            long durationMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started);
            appendEvent("progress", "solve", "finished", true, verified, false, "COMSOL study.run returned successfully",
                values("stage", "solve", "message", "Study finished", "progress_source", "study.run", "duration_ms", durationMs));
            send(exchange, verified ? 200 : 409, "{" + field("status", verified ? "finished" : "unverified") + "," +
                field("study_tag", studyTag) + "," + number("duration_ms", durationMs) + "," +
                number("last_computation_date", computationDate) + "," + bool("command_success", true) + "," +
                bool("model_state_verified", verified) + "," + bool("scientific_validation_passed", false) + "}");
        } catch (ReflectiveOperationException | RuntimeException exc) {
            appendEvent("progress", "solve", "failed", false, false, false, rootCause(exc),
                values("stage", "solve", "message", "Study failed", "progress_source", "study.run"));
            send(exchange, 500, failure("run_study", rootCause(exc)));
        }
    }

    /** Raw post-solve numerical field. Scientific checks remain in Python. */
    private static void heatField(HttpExchange exchange) throws IOException {
        if (!method(exchange, "GET")) return;
        Probe probe = Probe.detect();
        if (!probe.connected || LAST_STUDY_DATE <= 0L) {
            send(exchange, 503, failure("heat_field", "No current Bridge study solve is available"));
            return;
        }
        try {
            Object model = RUNTIME.currentModel();
            Object result = invoke(model, "result", new Class<?>[]{});
            double[] x = evaluateField(result, "x");
            double[] y = evaluateField(result, "y");
            double[] temperature = evaluateField(result, "T");
            if (x.length == 0 || x.length != y.length || x.length != temperature.length || x.length > 200_000) {
                send(exchange, 409, failure("heat_field", "Numerical field arrays are missing or misaligned"));
                return;
            }
            String payload = "{" + field("status", "readback") + "," +
                field("model_tag", probe.modelTag) + "," + number("last_computation_date", LAST_STUDY_DATE) + "," +
                number("sample_count", x.length) + "," + doubles("x", x) + "," + doubles("y", y) + "," +
                doubles("temperature_K", temperature) + "," + bool("command_success", true) + "," +
                bool("model_state_verified", true) + "," + bool("scientific_validation_passed", false) + "}";
            send(exchange, 200, payload);
        } catch (ReflectiveOperationException | RuntimeException exc) {
            send(exchange, 500, failure("heat_field", rootCause(exc)));
        }
    }

    /** Actual core Heat objects; no plan text and no optional diagnostics. */
    private static void heatCore(HttpExchange exchange) throws IOException {
        if (!method(exchange, "GET")) return;
        Probe probe = Probe.detect();
        if (!probe.connected) {
            send(exchange, 503, failure("heat_core", probe.detail));
            return;
        }
        try {
            Object model = RUNTIME.currentModel();
            Object component = invoke(model, "component", new Class<?>[]{String.class}, "comp1");
            Object geometry = invoke(component, "geom", new Class<?>[]{String.class}, "geom1");
            Object rectangle = invoke(geometry, "feature", new Class<?>[]{String.class}, "r1");
            Object material = invoke(model, "material", new Class<?>[]{String.class}, "mat1");
            Object physics = invoke(component, "physics", new Class<?>[]{String.class}, "ht");
            Object solid = invoke(physics, "feature", new Class<?>[]{String.class}, "solid1");
            List<Double> size = new ArrayList<>();
            flattenFinite(invoke(rectangle, "getDoubleArray", new Class<?>[]{String.class}, "size"), size);
            if (size.size() != 2 || !nestedTags(model, "study").contains("std1")
                    || !nestedTags(model, "sol").contains("sol1")
                    || !nestedTags(component, "mesh").contains("mesh1")) {
                send(exchange, 409, failure("heat_core", "Core Heat geometry, mesh, study or solver readback incomplete"));
                return;
            }
            String payload = "{" + field("status", "readback") + "," + field("model_tag", probe.modelTag) + "," +
                field("geometry_tag", "comp1:geom1") + "," + field("geometry_feature_tag", "r1") + "," +
                field("geometry_kind", String.valueOf(invoke(rectangle, "getType", new Class<?>[]{}))) + "," +
                field("geometry_unit", String.valueOf(invoke(geometry, "lengthUnit", new Class<?>[]{}))) + "," +
                "\"geometry_size\":[" + size.get(0) + "," + size.get(1) + "]," +
                number("geometry_domains", ((Number) invoke(geometry, "getNDomains", new Class<?>[]{})).longValue()) + "," +
                number("geometry_edges", ((Number) invoke(geometry, "getNEdges", new Class<?>[]{})).longValue()) + "," +
                field("material_tag", "mat1") + "," +
                field("material_name", String.valueOf(invoke(material, "label", new Class<?>[]{}))) + "," +
                field("material_origin", "user") + "," +
                field("k_expression", String.valueOf(invoke(solid, "getString", new Class<?>[]{String.class}, "k"))) + "," +
                field("rho_expression", String.valueOf(invoke(solid, "getString", new Class<?>[]{String.class}, "rho"))) + "," +
                field("Cp_expression", String.valueOf(invoke(solid, "getString", new Class<?>[]{String.class}, "Cp"))) + "," +
                field("physics_tag", "comp1:ht") + "," +
                field("physics_type", String.valueOf(invoke(physics, "getType", new Class<?>[]{}))) + "," +
                field("study_tag", "std1") + "," + field("study_type", "stationary") + "," +
                field("solver_tag", "sol1") + "," + field("mesh_tag", "comp1:mesh1") + "," +
                bool("command_success", true) + "," + bool("model_state_verified", true) + "," +
                bool("scientific_validation_passed", false) + "}";
            send(exchange, 200, payload);
        } catch (ReflectiveOperationException | RuntimeException exc) {
            send(exchange, 500, failure("heat_core", rootCause(exc)));
        }
    }

    /** Export only the loaded Heat model into a new attempt-scoped sibling. */
    private static void heatExport(HttpExchange exchange) throws IOException {
        if (!method(exchange, "POST")) return;
        Probe probe = Probe.detect();
        Map<String, String> data = parseFlatJson(readBody(exchange));
        String runId = data.getOrDefault("run_id", "");
        String attemptId = data.getOrDefault("attempt_id", "");
        if (!probe.connected || LAST_STUDY_DATE <= 0L || RUNTIME.loadedModelPath == null
                || !runId.matches("[A-Za-z0-9-]{1,100}") || !attemptId.matches("[A-Za-z0-9-]{1,100}")) {
            send(exchange, 400, failure("heat_export", "Current solve, loaded source, or safe run identity is unavailable"));
            return;
        }
        // The loaded MPH already lives beneath this run/attempt's isolated
        // workspace. A short sibling path avoids COMSOL's Windows export path
        // limits while retaining the Python-side run/attempt containment gate.
        Path directory = RUNTIME.loadedModelPath.getParent();
        Path modelPath = directory.resolve("heat_corrected.mph");
        Path imagePath = directory.resolve("temperature.png");
        if (Files.exists(modelPath) || Files.exists(imagePath)) {
            send(exchange, 409, failure("heat_export", "Corrected artifacts already exist; overwrite refused"));
            return;
        }
        try {
            Files.createDirectories(directory);
            Object model = RUNTIME.currentModel();
            Object result = invoke(model, "result", new Class<?>[]{});
            Object exports = invoke(result, "export", new Class<?>[]{});
            Object export;
            if (toStrings(invoke(exports, "tags", new Class<?>[]{})).contains("img_workbench")) {
                export = invoke(exports, "get", new Class<?>[]{String.class}, "img_workbench");
            } else {
                export = invoke(exports, "create", new Class<?>[]{String.class, String.class}, "img_workbench", "Image");
                invoke(export, "set", new Class<?>[]{String.class, String.class}, "plotgroup", "pg_workbench");
                invoke(export, "set", new Class<?>[]{String.class, String.class}, "imagetype", "png");
            }
            invoke(export, "set", new Class<?>[]{String.class, String.class}, "filename", imagePath.toString());
            invoke(export, "run", new Class<?>[]{});
            invoke(model, "save", new Class<?>[]{String.class}, modelPath.toString());
            if (!Files.isRegularFile(modelPath) || !Files.isRegularFile(imagePath)
                    || Files.size(modelPath) == 0 || Files.size(imagePath) == 0) {
                send(exchange, 409, failure("heat_export", "COMSOL artifacts were not fully written"));
                return;
            }
            send(exchange, 200, "{" + field("status", "exported") + "," + field("run_id", runId) + "," +
                field("attempt_id", attemptId) + "," + field("model", modelPath.toString()) + "," +
                field("temperature_image", imagePath.toString()) + "," +
                number("last_computation_date", LAST_STUDY_DATE) + "," + bool("command_success", true) + "," +
                bool("model_state_verified", true) + "," + bool("scientific_validation_passed", false) + "}");
        } catch (ReflectiveOperationException | RuntimeException exc) {
            appendEvent("critical_failure", "heat_export", "error", false, false, false, rootCause(exc));
            send(exchange, 500, failure("heat_export", rootCause(exc)));
        }
    }

    private static double[] evaluateField(Object result, String expression) throws ReflectiveOperationException {
        Object numericals = invoke(result, "numerical", new Class<?>[]{});
        String tag = "wb_rc42_" + expression;
        if (toStrings(invoke(numericals, "tags", new Class<?>[]{})).contains(tag))
            throw new IllegalStateException("Temporary numerical tag already exists");
        Object evaluation = invoke(numericals, "create", new Class<?>[]{String.class, String.class}, tag, "Eval");
        try {
            invoke(evaluation, "set", new Class<?>[]{String.class, String.class}, "expr", expression);
            Object data = invoke(evaluation, "getData", new Class<?>[]{});
            List<Double> numbers = new ArrayList<>();
            flattenFinite(data, numbers);
            double[] output = new double[numbers.size()];
            for (int i = 0; i < output.length; i++) output[i] = numbers.get(i);
            return output;
        } finally {
            invoke(numericals, "remove", new Class<?>[]{String.class}, tag);
        }
    }

    private static void flattenFinite(Object data, List<Double> output) {
        if (data == null) throw new IllegalStateException("Null COMSOL numerical data");
        if (data.getClass().isArray()) {
            for (int i = 0; i < Array.getLength(data); i++) flattenFinite(Array.get(data, i), output);
        } else if (data instanceof Number) {
            double number = ((Number) data).doubleValue();
            if (!Double.isFinite(number)) throw new IllegalStateException("Non-finite COMSOL numerical data");
            output.add(number);
        } else throw new IllegalStateException("Unsupported COMSOL numerical data shape");
    }

    private static String doubles(String key, double[] values) {
        StringBuilder encoded = new StringBuilder("\"").append(escape(key)).append("\":[");
        for (int i = 0; i < values.length; i++) {
            if (i > 0) encoded.append(',');
            encoded.append(values[i]);
        }
        return encoded.append(']').toString();
    }

    /** Narrow, compare-and-set Heat T0 API; no arbitrary physics/property mutation. */
    private static void boundaryFeature(HttpExchange exchange) throws IOException {
        String requestMethod = exchange.getRequestMethod();
        if (!"GET".equalsIgnoreCase(requestMethod) && !"POST".equalsIgnoreCase(requestMethod)) {
            send(exchange, 405, failure("boundary_feature", "Method not allowed"));
            return;
        }
        Probe probe = Probe.detect();
        if (!probe.connected) {
            send(exchange, 503, failure("boundary_feature", probe.detail));
            return;
        }
        Map<String, String> data = "GET".equalsIgnoreCase(requestMethod)
            ? query(exchange.getRequestURI()) : parseFlatJson(readBody(exchange));
        String componentTag = data.getOrDefault("component_tag", "");
        String physicsTag = data.getOrDefault("physics_tag", "");
        String featureTag = data.getOrDefault("feature_tag", "");
        String propertyKey = data.getOrDefault("property_key", "");
        if (!"comp1".equals(componentTag) || !"ht".equals(physicsTag)
                || !("temp_left".equals(featureTag) || "temp_right".equals(featureTag))
                || !"T0".equals(propertyKey)) {
            send(exchange, 400, failure("boundary_feature", "Only comp1:ht temp_left/temp_right TemperatureBoundary.T0 is supported"));
            return;
        }
        try {
            Object model = invokeStatic(probe.modelUtil, "model", new Class<?>[]{String.class}, probe.modelTag);
            Object component = invoke(model, "component", new Class<?>[]{String.class}, componentTag);
            Object physics = invoke(component, "physics", new Class<?>[]{String.class}, physicsTag);
            if (!nestedTags(physics, "feature").contains(featureTag)) {
                send(exchange, 404, failure("boundary_feature", "Boundary feature tag is absent"));
                return;
            }
            Object feature = invoke(physics, "feature", new Class<?>[]{String.class}, featureTag);
            String featureType = String.valueOf(invoke(feature, "getType", new Class<?>[]{}));
            Object selection = invoke(feature, "selection", new Class<?>[]{});
            List<Integer> entities = toIntegers(invoke(selection, "entities", new Class<?>[]{}));
            String current = String.valueOf(invoke(feature, "getString", new Class<?>[]{String.class}, "T0"));
            if (!"TemperatureBoundary".equals(featureType) || entities.size() != 1
                    || entities.get(0) <= 0 || !literalKelvin(current)) {
                send(exchange, 409, failure("boundary_feature", "Actual feature type, selection, or literal Kelvin value is not supported"));
                return;
            }
            if ("POST".equalsIgnoreCase(requestMethod)) {
                int expectedSelection;
                try { expectedSelection = Integer.parseInt(data.getOrDefault("expected_selection", "")); }
                catch (NumberFormatException exc) {
                    send(exchange, 400, failure("boundary_feature", "Expected selection is invalid"));
                    return;
                }
                String expected = data.getOrDefault("expected_expression", "");
                String proposed = data.getOrDefault("proposed_expression", "");
                if (entities.get(0) != expectedSelection || !expressionsEqual(current, expected)
                        || !literalKelvin(expected) || !literalKelvin(proposed)) {
                    send(exchange, 409, failure("boundary_feature", "Feature selection/value drift or invalid proposed Kelvin value"));
                    return;
                }
                invoke(feature, "set", new Class<?>[]{String.class, String.class}, "T0", proposed);
                String after = String.valueOf(invoke(feature, "getString", new Class<?>[]{String.class}, "T0"));
                List<Integer> afterEntities = toIntegers(invoke(
                    invoke(feature, "selection", new Class<?>[]{}), "entities", new Class<?>[]{}));
                if (!expressionsEqual(after, proposed) || !afterEntities.equals(entities)) {
                    send(exchange, 409, failure("boundary_feature", "Post-mutation feature readback mismatch"));
                    return;
                }
                current = after;
                appendEvent("model_change", "boundary_feature", "verified", true, true, false,
                    "COMSOL TemperatureBoundary.T0 changed and directly read back",
                    values("target", "comp1:ht:" + featureTag, "action", "set_T0",
                        "old_value", expected, "new_value", after, "detection_method", "direct_readback"));
            }
            String payload = "{" + field("status", "verified") + "," +
                field("model_tag", probe.modelTag) + "," + field("component_tag", componentTag) + "," +
                field("physics_tag", physicsTag) + "," + field("feature_tag", featureTag) + "," +
                field("feature_type", featureType) + "," + field("property_key", propertyKey) + "," +
                integerArray("selection", entities) + "," + field("expression", current) + "," +
                field("unit", "K") + "," + bool("command_success", true) + "," +
                bool("model_state_verified", true) + "," + bool("scientific_validation_passed", false) + "}";
            send(exchange, 200, payload);
        } catch (ReflectiveOperationException | RuntimeException exc) {
            appendEvent("critical_failure", "boundary_feature", "error", false, false, false, rootCause(exc));
            send(exchange, 500, failure("boundary_feature", rootCause(exc)));
        }
    }

    private static boolean literalKelvin(String value) {
        Matcher match = LITERAL_KELVIN.matcher(value);
        if (!match.matches()) return false;
        try {
            double temperature = Double.parseDouble(match.group(1));
            return Double.isFinite(temperature) && temperature > 0;
        } catch (NumberFormatException exc) { return false; }
    }

    private static List<Integer> toIntegers(Object values) {
        List<Integer> result = new ArrayList<>();
        for (String value : toStrings(values)) result.add(Integer.parseInt(value));
        return result;
    }

    private static void testFeature(HttpExchange exchange) throws IOException {
        if (!method(exchange, "POST")) return;
        Probe probe = Probe.detect();
        if (!probe.connected) {
            appendEvent("command", "test_feature", "unavailable", false, false, false, probe.detail);
            send(exchange, 503, failure("test_feature", probe.detail));
            return;
        }
        String body = readBody(exchange);
        Map<String, String> values = parseFlatJson(body);
        String modelTag = values.get("model_tag");
        String componentTag = values.getOrDefault("component_tag", "comp1");
        String geometryTag = values.getOrDefault("geometry_tag", "geom1");
        String featureTag = values.getOrDefault("feature_tag", "wb_probe_1");
        String featureType = values.getOrDefault("feature_type", "Point");
        if (modelTag == null || !featureTag.startsWith("wb_probe_")) {
            send(exchange, 400, failure("test_feature", "model_tag is required and feature_tag must start with wb_probe_"));
            return;
        }
        appendEvent("command", "test_feature", "running", false, false, false, "Creating scoped COMSOL probe feature");
        try {
            Object model = invokeStatic(probe.modelUtil, "model", new Class<?>[]{String.class}, modelTag);
            Object component = invoke(model, "component", new Class<?>[]{String.class}, componentTag);
            Object geometry = invoke(component, "geom", new Class<?>[]{String.class}, geometryTag);
            Object features = invoke(geometry, "feature", new Class<?>[]{});
            invoke(features, "create", new Class<?>[]{String.class, String.class}, featureTag, featureType);
            List<String> tags = toStrings(invoke(features, "tags", new Class<?>[]{}));
            boolean verified = tags.contains(featureTag);
            appendEvent("model_change", "test_feature", verified ? "verified" : "unverified", true, verified, false,
                verified ? "Feature present in COMSOL readback" : "Create returned but tag was absent from readback");
            String payload = "{" + field("status", verified ? "verified" : "unverified") + "," +
                field("feature_tag", featureTag) + "," + bool("command_success", true) + "," +
                bool("model_state_verified", verified) + "," + bool("scientific_validation_passed", false) + "}";
            send(exchange, verified ? 200 : 409, payload);
        } catch (ReflectiveOperationException | RuntimeException exc) {
            appendEvent("critical_failure", "test_feature", "error", false, false, false, exc.toString());
            send(exchange, 500, failure("test_feature", exc.toString()));
        }
    }

    private static void progress(HttpExchange exchange) throws IOException {
        if (!method(exchange, "POST")) return;
        Map<String, String> values = parseFlatJson(readBody(exchange));
        String phase = values.getOrDefault("phase", "unknown");
        String status = values.getOrDefault("status", "running");
        String detail = values.getOrDefault("detail", "COMSOL-side progress update");
        appendEvent("progress", phase, status, false, false, false, detail);
        send(exchange, 202, "{" + field("status", "accepted") + "," + bool("command_success", true) + "," +
            bool("model_state_verified", false) + "," + bool("scientific_validation_passed", false) + "}");
    }

    private static void events(HttpExchange exchange) throws IOException {
        if (!method(exchange, "GET")) return;
        long after = 0;
        try { after = Long.parseLong(query(exchange.getRequestURI()).getOrDefault("after", "0")); }
        catch (NumberFormatException ignored) { after = 0; }
        List<String> selected = new ArrayList<>();
        synchronized (EVENTS) {
            for (String event : EVENTS) {
                Matcher matcher = Pattern.compile("\\\"sequence\\\":([0-9]+)").matcher(event);
                if (matcher.find() && Long.parseLong(matcher.group(1)) > after) selected.add(event);
            }
        }
        send(exchange, 200, "{" + number("latest_sequence", SEQUENCE.get()) + ",\"events\":[" + String.join(",", selected) + "]}");
    }

    private static void appendEvent(String type, String phase, String status, boolean command, boolean model, boolean scientific, String detail) {
        appendEvent(type, phase, status, command, model, scientific, detail, Collections.emptyMap());
    }

    private static void appendEvent(String type, String phase, String status, boolean command, boolean model, boolean scientific, String detail, Map<String, ?> extra) {
        long sequence = SEQUENCE.incrementAndGet();
        StringBuilder event = new StringBuilder("{").append(number("sequence", sequence)).append(",")
            .append(field("type", type)).append(",").append(field("event_type", type)).append(",")
            .append(field("phase", phase)).append(",").append(field("status", status)).append(",")
            .append(bool("command_success", command)).append(",").append(bool("model_state_verified", model)).append(",")
            .append(bool("scientific_validation_passed", scientific)).append(",").append(field("detail", detail)).append(",")
            .append(field("created_at", Instant.now().toString()));
        for (Map.Entry<String, ?> item : extra.entrySet()) {
            event.append(",").append(jsonValue(item.getKey(), item.getValue()));
        }
        event.append("}");
        synchronized (EVENTS) {
            EVENTS.addLast(event.toString());
            while (EVENTS.size() > MAX_EVENTS) EVENTS.removeFirst();
        }
    }

    private static String failure(String phase, String detail) {
        return "{" + field("status", "unavailable") + "," + field("phase", phase) + "," + field("detail", detail) + "," +
            bool("command_success", false) + "," + bool("model_state_verified", false) + "," +
            bool("scientific_validation_passed", false) + "}";
    }

    private static List<String> nestedTags(Object model, String method) throws ReflectiveOperationException {
        Object collection = invoke(model, method, new Class<?>[]{});
        return toStrings(invoke(collection, "tags", new Class<?>[]{}));
    }

    private static void addPrefixed(List<String> target, String parent, List<String> tags) {
        for (String tag : tags) target.add(parent + ":" + tag);
    }

    private static Map<String, String> readParameters(Object model) throws ReflectiveOperationException {
        Object parameters = invoke(model, "param", new Class<?>[]{});
        List<String> names = toStrings(invoke(parameters, "varnames", new Class<?>[]{}));
        Collections.sort(names);
        Map<String, String> result = new LinkedHashMap<>();
        for (String name : names) {
            result.put(name, String.valueOf(invoke(parameters, "get", new Class<?>[]{String.class}, name)));
        }
        return result;
    }

    private static boolean expressionsEqual(String requested, String readback) {
        return requested.replaceAll("\\s+", "").equals(readback.replaceAll("\\s+", ""));
    }

    private static Map<String, Object> values(Object... pairs) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (int index = 0; index + 1 < pairs.length; index += 2) {
            result.put(String.valueOf(pairs[index]), pairs[index + 1]);
        }
        return result;
    }

    private static Object invoke(Object target, String name, Class<?>[] types, Object... values) throws ReflectiveOperationException {
        Method method = target.getClass().getMethod(name, types);
        return method.invoke(target, values);
    }

    private static Object invokeStatic(Class<?> target, String name, Class<?>[] types, Object... values) throws ReflectiveOperationException {
        Method method = target.getMethod(name, types);
        return method.invoke(null, values);
    }

    private static List<String> toStrings(Object value) {
        List<String> result = new ArrayList<>();
        if (value == null) return result;
        if (value.getClass().isArray()) {
            for (int index = 0; index < Array.getLength(value); index++) result.add(String.valueOf(Array.get(value, index)));
        } else if (value instanceof Iterable<?>) {
            for (Object item : (Iterable<?>) value) result.add(String.valueOf(item));
        } else result.add(String.valueOf(value));
        return result;
    }

    private static Map<String, String> parseFlatJson(String body) {
        Map<String, String> values = new HashMap<>();
        Matcher strings = JSON_STRING.matcher(body);
        while (strings.find()) values.put(strings.group(1), strings.group(2));
        Matcher numbers = JSON_NUMBER.matcher(body);
        while (numbers.find()) values.put(numbers.group(1), numbers.group(2));
        return values;
    }

    private static Map<String, String> query(URI uri) {
        Map<String, String> values = new HashMap<>();
        String raw = uri.getRawQuery();
        if (raw == null || raw.isBlank()) return values;
        for (String item : raw.split("&")) {
            String[] pair = item.split("=", 2);
            values.put(
                URLDecoder.decode(pair[0], StandardCharsets.UTF_8),
                pair.length > 1 ? URLDecoder.decode(pair[1], StandardCharsets.UTF_8) : ""
            );
        }
        return values;
    }

    private static String readBody(HttpExchange exchange) throws IOException {
        try (InputStream input = exchange.getRequestBody()) {
            return new String(input.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private static boolean method(HttpExchange exchange, String expected) throws IOException {
        if (expected.equalsIgnoreCase(exchange.getRequestMethod())) return true;
        send(exchange, 405, "{" + field("status", "error") + "," + field("detail", "Method not allowed") + "}");
        return false;
    }

    private static void send(HttpExchange exchange, int status, String payload) throws IOException {
        byte[] bytes = payload.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = exchange.getResponseBody()) { output.write(bytes); }
    }

    private static String field(String key, String value) { return "\"" + escape(key) + "\":\"" + escape(value == null ? "" : value) + "\""; }
    private static String bool(String key, boolean value) { return "\"" + escape(key) + "\":" + value; }
    private static String number(String key, long value) { return "\"" + escape(key) + "\":" + value; }
    private static String array(String key, List<String> values) {
        List<String> encoded = new ArrayList<>();
        for (String value : values) encoded.add("\"" + escape(value) + "\"");
        return "\"" + escape(key) + "\":[" + String.join(",", encoded) + "]";
    }
    private static String integerArray(String key, List<Integer> values) {
        List<String> encoded = new ArrayList<>();
        for (Integer value : values) encoded.add(String.valueOf(value));
        return "\"" + escape(key) + "\":[" + String.join(",", encoded) + "]";
    }
    private static String object(String key, Map<String, String> values) {
        List<String> encoded = new ArrayList<>();
        for (Map.Entry<String, String> item : values.entrySet()) encoded.add(field(item.getKey(), item.getValue()));
        return "\"" + escape(key) + "\":{" + String.join(",", encoded) + "}";
    }
    private static String jsonValue(String key, Object value) {
        if (value == null) return "\"" + escape(key) + "\":null";
        if (value instanceof Boolean) return bool(key, (Boolean) value);
        if (value instanceof Number) return "\"" + escape(key) + "\":" + value;
        return field(key, String.valueOf(value));
    }
    private static String escape(String value) { return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\r", "\\r").replace("\n", "\\n"); }

    private static int argumentPort(String[] args, int fallback) {
        for (int index = 0; index + 1 < args.length; index++) {
            if ("--port".equals(args[index])) return Integer.parseInt(args[index + 1]);
        }
        return fallback;
    }

    private static String argument(String[] args, String name, String fallback) {
        for (int index = 0; index + 1 < args.length; index++) {
            if (name.equals(args[index])) return args[index + 1];
        }
        return fallback;
    }

    private static final class RuntimeState {
        volatile boolean apiLoaded;
        volatile boolean initialized;
        volatile String detail = "COMSOL runtime has not been initialized";
        volatile String mode = "contract";
        volatile String comsolVersion = "";
        volatile String modelTag = "";
        volatile Path loadedModelPath;
        volatile String detectionMethod = "unavailable";
        volatile long pollMs = 1000L;
        volatile Class<?> modelUtil;
        volatile Object modelChangedHandler;
        volatile boolean lastConnected;
        private final Map<String, String> parameterSnapshot = new LinkedHashMap<>();
        private final Object progressLogLock = new Object();
        private ScheduledExecutorService watcher;
        private ScheduledExecutorService progressWatcher;
        private Path progressLog;
        private long progressLogOffset;

        void initialize(String[] args) {
            mode = argument(args, "--mode", "contract");
            pollMs = Math.max(500L, Math.min(2000L, Long.parseLong(argument(args, "--poll-ms", "1000"))));
            try {
                modelUtil = Class.forName("com.comsol.model.util.ModelUtil");
                apiLoaded = true;
                if ("standalone".equalsIgnoreCase(mode)) {
                    invokeStatic(modelUtil, "initStandalone", new Class<?>[]{boolean.class}, false);
                    initialized = true;
                } else if ("server".equalsIgnoreCase(mode)) {
                    String host = argument(args, "--server-host", "127.0.0.1");
                    int serverPort = Integer.parseInt(argument(args, "--server-port", "2036"));
                    invokeStatic(modelUtil, "connect", new Class<?>[]{String.class, int.class}, host, serverPort);
                    initialized = true;
                }
                String modelPath = argument(args, "--model-path", "");
                String requestedTag = argument(args, "--model-tag", "workbench_model");
                if (!modelPath.isBlank()) {
                    invokeStatic(modelUtil, "load", new Class<?>[]{String.class, String.class}, requestedTag, modelPath);
                    modelTag = requestedTag;
                    loadedModelPath = Paths.get(modelPath).toAbsolutePath().normalize();
                }
                String progressLog = argument(args, "--progress-log", "");
                if (!progressLog.isBlank()) {
                    invokeStatic(modelUtil, "showProgress", new Class<?>[]{String.class}, progressLog);
                    this.progressLog = Paths.get(progressLog);
                }
                refresh();
                installModelChangedHandler();
                if (!modelTag.isBlank()) {
                    replaceParameterSnapshot(readParameters(currentModel()));
                    lastConnected = true;
                }
                startWatcher();
            } catch (ClassNotFoundException exc) {
                apiLoaded = false;
                detail = "COMSOL ModelUtil is not on the Java Bridge classpath";
            } catch (ReflectiveOperationException | RuntimeException exc) {
                detail = "COMSOL runtime initialization failed: " + rootCause(exc);
            }
        }

        void refresh() {
            if (!apiLoaded || modelUtil == null) return;
            try {
                comsolVersion = String.valueOf(invokeStatic(modelUtil, "getComsolVersion", new Class<?>[]{}));
                List<String> tags = toStrings(invokeStatic(modelUtil, "tags", new Class<?>[]{}));
                if (!tags.isEmpty() && (modelTag.isBlank() || !tags.contains(modelTag))) modelTag = tags.get(0);
                detail = tags.isEmpty()
                    ? "Real COMSOL ModelUtil loaded; no model is connected"
                    : "Real COMSOL ModelUtil and model object read back";
            } catch (ReflectiveOperationException | RuntimeException exc) {
                modelTag = "";
                detail = "COMSOL runtime probe failed: " + rootCause(exc);
            }
        }

        Object currentModel() throws ReflectiveOperationException {
            refresh();
            if (modelUtil == null || modelTag.isBlank()) throw new IllegalStateException("No real COMSOL model is connected");
            return invokeStatic(modelUtil, "model", new Class<?>[]{String.class}, modelTag);
        }

        synchronized void replaceParameterSnapshot(Map<String, String> current) {
            parameterSnapshot.clear();
            parameterSnapshot.putAll(current);
        }

        private void installModelChangedHandler() {
            if (!initialized || modelUtil == null) return;
            try {
                Class<?> handlerType = Class.forName("com.comsol.model.util.ModelChangedHandler");
                InvocationHandler invocation = (proxy, method, values) -> {
                    if ("triggeredByChangesFromThisClient".equals(method.getName())) return true;
                    if ("handleModelChangeOnServer".equals(method.getName())) {
                        checkForChanges("native_model_changed_handler", true);
                        return null;
                    }
                    if ("toString".equals(method.getName())) return "WorkbenchModelChangedHandler";
                    return null;
                };
                modelChangedHandler = Proxy.newProxyInstance(handlerType.getClassLoader(), new Class<?>[]{handlerType}, invocation);
                invokeStatic(modelUtil, "setModelChangedHandler", new Class<?>[]{handlerType}, modelChangedHandler);
                detectionMethod = "native_model_changed_handler+polling_fallback";
            } catch (ReflectiveOperationException | RuntimeException exc) {
                detectionMethod = "polling_fallback";
                appendEvent("command", "model_watch", "warning", false, false, false,
                    "Native model change handler unavailable; polling fallback active",
                    values("detection_method", detectionMethod, "error", rootCause(exc)));
            }
        }

        private void startWatcher() {
            watcher = Executors.newSingleThreadScheduledExecutor(runnable -> {
                Thread thread = new Thread(runnable, "comsol-model-watch");
                thread.setDaemon(true);
                return thread;
            });
            watcher.scheduleWithFixedDelay(
                () -> checkForChanges("polling_fallback", false), pollMs, pollMs, TimeUnit.MILLISECONDS);
            progressWatcher = Executors.newSingleThreadScheduledExecutor(runnable -> {
                Thread thread = new Thread(runnable, "comsol-progress-log-watch");
                thread.setDaemon(true);
                return thread;
            });
            long progressPollMs = Math.max(100L, Math.min(250L, pollMs / 4L));
            progressWatcher.scheduleWithFixedDelay(this::pollProgressLog, progressPollMs, progressPollMs, TimeUnit.MILLISECONDS);
        }

        void pollProgressLog() {
            synchronized (progressLogLock) {
                if (progressLog == null || !java.nio.file.Files.isRegularFile(progressLog)) return;
                try (RandomAccessFile file = new RandomAccessFile(progressLog.toFile(), "r")) {
                long length = file.length();
                if (length < progressLogOffset) progressLogOffset = 0;
                if (length == progressLogOffset) return;
                int available = (int) Math.min(256_000L, length - progressLogOffset);
                byte[] bytes = new byte[available];
                file.seek(progressLogOffset);
                file.readFully(bytes);
                progressLogOffset += available;
                String text = new String(bytes, StandardCharsets.UTF_8);
                for (String raw : text.split("\\R")) {
                    String line = raw.trim();
                    if (line.isEmpty() || line.startsWith("Memory:")) continue;
                    Matcher progress = PROGRESS_LINE.matcher(line);
                    if (progress.find()) {
                        int percent = Integer.parseInt(progress.group(1));
                        appendEvent("progress", "solve", "progress_changed", true, false, false,
                            progress.group(2).isBlank() ? "COMSOL solver progress" : progress.group(2),
                            values("stage", "solve", "progress", percent / 100.0, "percent", percent,
                                "message", line, "progress_source", "ModelUtil.showProgress"));
                    } else if (line.startsWith("<----") || line.endsWith("----->") || line.startsWith("Iter") ||
                            line.startsWith("开始于") || line.startsWith("结束时间") || line.startsWith("求解的自由度数") ||
                            line.startsWith("Started at") || line.startsWith("Ended at")) {
                        appendEvent("progress", "solve", "log_message", true, false, false, line,
                            values("stage", "solve", "message", line, "progress_source", "ModelUtil.showProgress"));
                    }
                }
                } catch (IOException | RuntimeException exc) {
                    appendEvent("command", "progress_log", "warning", false, false, false,
                        "Progress log read failed: " + rootCause(exc), values("progress_source", "ModelUtil.showProgress"));
                }
            }
        }

        synchronized void checkForChanges(String method, boolean nativeSignal) {
            if (!apiLoaded || modelUtil == null) return;
            try {
                refresh();
                boolean connected = !modelTag.isBlank();
                if (!connected) {
                    if (lastConnected) {
                        appendEvent("model_change", "connection", "disconnected", false, false, false,
                            "Real COMSOL model disconnected",
                            values("target", "model", "action", "disconnected", "verified", true, "detection_method", method));
                    }
                    lastConnected = false;
                    return;
                }
                Object model = currentModel();
                Map<String, String> current = readParameters(model);
                Set<String> names = new LinkedHashSet<>();
                names.addAll(parameterSnapshot.keySet());
                names.addAll(current.keySet());
                boolean changed = false;
                for (String name : names) {
                    String oldValue = parameterSnapshot.get(name);
                    String newValue = current.get(name);
                    if (String.valueOf(oldValue).equals(String.valueOf(newValue))) continue;
                    changed = true;
                    String action = oldValue == null ? "created" : newValue == null ? "removed" : "updated";
                    appendEvent("model_change", "parameter", "verified", false, true, false,
                        "COMSOL parameter change detected from real model object",
                        values("target", "param:" + name, "action", action, "verified", true,
                            "old_value", oldValue == null ? "" : oldValue,
                            "new_value", newValue == null ? "" : newValue,
                            "detection_method", method));
                }
                replaceParameterSnapshot(current);
                lastConnected = true;
                if (nativeSignal && !changed) {
                    appendEvent("model_change", "model", "detected", false, true, false,
                        "COMSOL native model change signal received",
                        values("target", "model:" + modelTag, "action", "changed", "verified", true, "detection_method", method));
                }
            } catch (ReflectiveOperationException | RuntimeException exc) {
                if (lastConnected) {
                    appendEvent("model_change", "connection", "disconnected", false, false, false,
                        "COMSOL model probe failed: " + rootCause(exc),
                        values("target", "model", "action", "disconnected", "verified", true, "detection_method", method));
                }
                lastConnected = false;
            }
        }
    }

    private static String rootCause(Throwable throwable) {
        Throwable current = throwable;
        while (current.getCause() != null && current.getCause() != current) current = current.getCause();
        return current.getClass().getName() + ": " + String.valueOf(current.getMessage());
    }

    private static final class Probe {
        final boolean apiLoaded;
        final boolean connected;
        final String detail;
        final Class<?> modelUtil;
        final List<String> modelTags;
        final String comsolVersion;
        final String modelTag;

        Probe(boolean apiLoaded, boolean connected, String detail, Class<?> modelUtil, List<String> modelTags, String comsolVersion, String modelTag) {
            this.apiLoaded = apiLoaded;
            this.connected = connected;
            this.detail = detail;
            this.modelUtil = modelUtil;
            this.modelTags = modelTags;
            this.comsolVersion = comsolVersion;
            this.modelTag = modelTag;
        }

        static Probe detect() {
            RUNTIME.refresh();
            if (RUNTIME.apiLoaded && RUNTIME.modelUtil != null) {
                try {
                    List<String> tags = toStrings(invokeStatic(RUNTIME.modelUtil, "tags", new Class<?>[]{}));
                    return new Probe(true, !tags.isEmpty(), RUNTIME.detail, RUNTIME.modelUtil, tags, RUNTIME.comsolVersion, RUNTIME.modelTag);
                } catch (ReflectiveOperationException | RuntimeException exc) {
                    return new Probe(true, false, "COMSOL ModelUtil probe failed: " + rootCause(exc), RUNTIME.modelUtil, List.of(), RUNTIME.comsolVersion, "");
                }
            }
            try {
                Class<?> utility = Class.forName("com.comsol.model.util.ModelUtil");
                List<String> tags = toStrings(invokeStatic(utility, "tags", new Class<?>[]{}));
                if (tags.isEmpty()) return new Probe(true, false, "COMSOL ModelUtil is available but no real model is attached", utility, tags, "", "");
                return new Probe(true, true, "Real COMSOL ModelUtil model tags were read back", utility, tags, "", tags.get(0));
            } catch (ClassNotFoundException exc) {
                return new Probe(false, false, "COMSOL ModelUtil is not on the Java Bridge classpath", null, List.of(), "", "");
            } catch (ReflectiveOperationException | RuntimeException exc) {
                return new Probe(true, false, "COMSOL ModelUtil probe failed: " + rootCause(exc), null, List.of(), "", "");
            }
        }
    }
}
