import { useState } from 'react'

export function ScientificTaskForm({ engine, zh, onPrepare }: { engine: string; zh: boolean; onPrepare: (source: string) => void }) {
  const [operation, setOperation] = useState('Integrate')
  const [expression, setExpression] = useState('Sin[x]')
  const [values, setValues] = useState({ width: 0.1, height: 0.05, left: 373.15, right: 293.15, conductivity: 238 })
  const [error, setError] = useState('')
  if (engine === 'Mathics3') return <section className="scientific-task-form">
    <h2>{zh ? '数学与物理计算' : 'Mathematics and physics'}</h2>
    <label>{zh ? '计算类型' : 'Operation'}<select value={operation} onChange={(event) => setOperation(event.target.value)}>
      <option value="Integrate">{zh ? '不定积分' : 'Integrate'}</option><option value="D">{zh ? '求导' : 'Differentiate'}</option>
      <option value="Simplify">{zh ? '化简' : 'Simplify'}</option><option value="Solve">{zh ? '解方程' : 'Solve'}</option>
    </select></label>
    <label>{zh ? '表达式（变量 x；方程使用 ==）' : 'Expression (variable x; == for equations)'}
      <input value={expression} onChange={(event) => setExpression(event.target.value)} /></label>
    <button type="button" onClick={() => onPrepare(`${operation}[${expression}${operation === 'Simplify' ? '' : ', x'}]`)}>{zh ? '生成本次计算' : 'Prepare calculation'}</button>
    <p>{zh ? '使用独立 Mathics3 内核；可在高级面板检查表达式。' : 'Uses the external Mathics3 kernel. Inspect the expression in the advanced panel.'}</p>
  </section>
  if (engine !== 'MATLAB') return null
  const prepare = () => {
    if (Object.values(values).some((value) => !Number.isFinite(value) || value <= 0)) {
      setError(zh ? '尺寸、绝对温度和导热系数必须为有限正数。' : 'Dimensions, absolute temperatures and conductivity must be finite and positive.')
      return
    }
    setError('')
    onPrepare(`assert(~isempty(ver('pde')) && license('test','PDE_Toolbox'), 'PDE Toolbox required');
model = createpde('thermal','steadystate');
w = ${values.width}; h = ${values.height};
rect = [3;4;0;w;w;0;0;0;h;h];
geometryFromEdges(model, decsg(rect));
thermalProperties(model,'ThermalConductivity',${values.conductivity});
assert(ismethod(model.Geometry,'nearestEdge'), 'Geometry edge lookup is required');
leftEdge = nearestEdge(model.Geometry,[0;h/2]);
rightEdge = nearestEdge(model.Geometry,[w;h/2]);
bottomEdge = nearestEdge(model.Geometry,[w/2;0]);
topEdge = nearestEdge(model.Geometry,[w/2;h]);
assert(numel(unique([leftEdge rightEdge bottomEdge topEdge])) == 4, 'Ambiguous boundary lookup');
thermalBC(model,'Edge',leftEdge,'Temperature',${values.left});
thermalBC(model,'Edge',rightEdge,'Temperature',${values.right});
thermalBC(model,'Edge',[bottomEdge topEdge],'HeatFlux',0);
generateMesh(model,'Hmax',min(w,h)/15);
result = solve(model);
assert(all(isfinite(result.Temperature)), 'Non-finite temperature');
expected = ${values.left} + (${values.right}-${values.left})*model.Mesh.Nodes(1,:)'/w;
relativeError = max(abs(result.Temperature-expected))/max(1,max(abs(expected)));
assert(relativeError <= 0.01, 'Analytical temperature error exceeds 1 percent');
disp(['Analytical relative error: ' num2str(relativeError)]);
fig = figure('Visible','off');
pdeplot(model,'XYData',result.Temperature,'Contour','on');
title('Stationary temperature (K)'); axis equal;
exportgraphics(fig,'temperature.png','Resolution',600);
writematrix([model.Mesh.Nodes' result.Temperature],'temperature.csv');
disp(version); disp(ver('pde'));
close(fig);`)
  }
  return <section className="scientific-task-form">
    <h2>{zh ? '二维稳态传热 · PDE Toolbox' : '2D stationary heat · PDE Toolbox'}</h2>
    <p>{zh ? '矩形、左右恒温、上下绝热、常数各向同性导热系数；以下为可修改的探索示例。实机尚未验证。' : 'Exploratory rectangle: fixed side temperatures, insulated top/bottom and constant isotropic conductivity. Live MATLAB validation pending.'}</p>
    {Object.entries(values).map(([name, value]) => <label key={name}>{name} ({name === 'width' || name === 'height' ? 'm' : name === 'conductivity' ? 'W/(m·K)' : 'K'})
      <input type="number" step="any" min="0" value={value} onChange={(event) => setValues({ ...values, [name]: Number(event.target.value) })} /></label>)}
    <button type="button" onClick={prepare}>{zh ? '生成本次模型' : 'Prepare model'}</button>
    {error && <p role="alert">{error}</p>}
  </section>
}
