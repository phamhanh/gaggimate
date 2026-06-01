import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faTrashCan } from '@fortawesome/free-solid-svg-icons/faTrashCan';
import { useEffect, useState } from 'preact/hooks';

export const TargetTypes = [
  {
    label: 'Water drawn',
    type: 'pumped',
    operator: 'gte',
    unit: 'ml',
  },
  {
    label: 'Current weight reached',
    type: 'weight',
    operator: 'gte',
    unit: 'g',
  },
  {
    label: 'Predicted weight reached',
    type: 'predicted_weight',
    operator: 'gte',
    unit: 'g',
  },
  {
    label: 'Pressure above',
    type: 'pressure',
    operator: 'gte',
    unit: 'bar',
  },
  {
    label: 'Pressure below',
    type: 'pressure',
    operator: 'lte',
    unit: 'bar',
  },
  {
    label: 'Flow above',
    type: 'flow',
    operator: 'gte',
    unit: 'ml/s',
  },
  {
    label: 'Flow below',
    type: 'flow',
    operator: 'lte',
    unit: 'ml/s',
  },
];

export function ExtendedPhaseTarget({ onChange, target, index, onRemove }) {
  const [draft, setDraft] = useState(null);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) {
      setDraft(null);
    }
  }, [focused, target.value, target.type, target.operator]);

  const normalizedType = target.type === 'volumetric' ? 'predicted_weight' : target.type;
  const targetType =
    TargetTypes.find(tt => tt.type === normalizedType && tt.operator === (target.operator || 'gte')) ||
    TargetTypes[0];

  const displayValue = draft !== null ? draft : target.value ?? 0;

  const commitDraft = () => {
    const raw = draft ?? '';
    const trimmed = String(raw).trim();
    let n = trimmed === '' ? NaN : parseFloat(trimmed);
    if (!Number.isFinite(n)) {
      n = 0;
    }
    n = Math.max(0, n);
    setDraft(null);
    setFocused(false);
    if (n !== target.value) {
      onChange({
        ...target,
        value: n,
      });
    }
  };

  return (
    <>
      <div className='grid grid-cols-1 gap-4'>
        <div className='form-control'>
          <label htmlFor={`phase-${index}-target-value`} className='mb-2 block text-sm font-medium'>
            {targetType.label}
          </label>
          <div className='flex flex-row gap-2'>
            <div className='input-group flex-grow'>
              <label htmlFor={`phase-${index}-target-value`} className='input w-full'>
                <input
                  id={`phase-${index}-target-value`}
                  className='grow'
                  type='number'
                  value={displayValue}
                  onFocus={() => {
                    setFocused(true);
                    setDraft(String(target.value ?? 0));
                  }}
                  onChange={e => setDraft(e.target.value)}
                  onBlur={() => commitDraft()}
                  aria-label={`Target value in ${targetType.unit}`}
                  min='0'
                  step='0.1'
                />
                <span aria-label={targetType.unit}>{targetType.unit}</span>
              </label>
            </div>
            <button
              type='button'
              className={`join-item btn btn-outline text-error`}
              aria-label='Remove target'
              onClick={() => onRemove()}
            >
              <FontAwesomeIcon icon={faTrashCan} />
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
