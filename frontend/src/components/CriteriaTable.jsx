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

