export function CriteriaTable({ checks = [] }) {
  return (
    <table className="criteria">
      <thead>
        <tr>
          <th>Criterion</th>
          <th>Required</th>
          <th>Verified value</th>
          <th>Result</th>
        </tr>
      </thead>
      <tbody>
        {checks.map((check) => (
          <tr key={check.criterion} className={check.passed ? 'pass' : 'fail'}>
            <td>
              <span className="criteria-label">{check.label}</span>
              <span className="criteria-source">{check.source}</span>
            </td>
            <td className="mono">{check.required_display}</td>
            <td className="mono">{check.actual_display}</td>
            <td>{check.passed ? '✓ met' : '✕ not met'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function ScoreBreakdown({ components = [], score, label }) {
  return (
    <div className="score">
      <div className="score-head">
        <span className="score-value">{score}</span>
        <span className="score-label">{label}</span>
        <span className="score-scale">/ 100</span>
      </div>
      <ul className="score-components">
        {components.map((component) => (
          <li key={component.key}>
            <span className="score-name">{component.label}</span>
            <span className="score-bar">
              <span
                className="score-fill"
                style={{ width: `${Math.round((component.ratio || 0) * 100)}%` }}
              />
            </span>
            <span className="score-points mono">
              {component.points}/{component.weight}
            </span>
            <span className="score-detail">{component.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
