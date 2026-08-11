import { useEffect, useRef, useState, type FormEvent } from 'react'

import type { ReviewSubmission } from './reviewTypes'

const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const SUPPORTED_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

type FieldName =
  | 'brandName'
  | 'classType'
  | 'expectedAbv'
  | 'expectedNetContents'
  | 'image'

type FieldErrors = Partial<Record<FieldName, string>>

interface ReviewFormProps {
  isSubmitting: boolean
  onSubmit: (submission: ReviewSubmission) => Promise<void>
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function ReviewForm({ isSubmitting, onSubmit }: ReviewFormProps) {
  const [brandName, setBrandName] = useState('')
  const [classType, setClassType] = useState('')
  const [expectedAbv, setExpectedAbv] = useState('')
  const [expectedNetContents, setExpectedNetContents] = useState('')
  const [expectedNetContentsUnit, setExpectedNetContentsUnit] = useState<'mL' | 'L'>('mL')
  const [image, setImage] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [errors, setErrors] = useState<FieldErrors>({})

  const brandRef = useRef<HTMLInputElement>(null)
  const classTypeRef = useRef<HTMLInputElement>(null)
  const abvRef = useRef<HTMLInputElement>(null)
  const netContentsRef = useRef<HTMLInputElement>(null)
  const imageRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [previewUrl])

  function setFieldError(field: FieldName, message?: string) {
    setErrors((current) => {
      const next = { ...current }
      if (message) next[field] = message
      else delete next[field]
      return next
    })
  }

  function handleImageChange(file: File | null) {
    setImage(file)
    const canPreview =
      file !== null &&
      file.size <= MAX_IMAGE_BYTES &&
      (!file.type || SUPPORTED_IMAGE_TYPES.has(file.type))
    setPreviewUrl(canPreview ? URL.createObjectURL(file) : null)

    if (!file) {
      setFieldError('image', 'Choose one label image or composite.')
    } else if (file.size > MAX_IMAGE_BYTES) {
      setFieldError('image', 'Choose an image no larger than 10 MB.')
    } else if (file.type && !SUPPORTED_IMAGE_TYPES.has(file.type)) {
      setFieldError('image', 'Choose a JPEG, PNG, or WebP image.')
    } else {
      setFieldError('image')
    }
  }

  function validate(): FieldErrors {
    const next: FieldErrors = {}
    const abv = Number(expectedAbv)
    const netContents = Number(expectedNetContents)

    if (!brandName.trim()) next.brandName = 'Enter the expected brand name.'
    else if (brandName.trim().length > 200) next.brandName = 'Use 200 characters or fewer.'

    if (!classType.trim()) next.classType = 'Enter the expected class or type.'
    else if (classType.trim().length > 200) next.classType = 'Use 200 characters or fewer.'

    if (!expectedAbv.trim() || !Number.isFinite(abv) || abv < 0 || abv > 100) {
      next.expectedAbv = 'Enter an ABV from 0 to 100.'
    }

    if (!expectedNetContents.trim() || !Number.isFinite(netContents) || netContents <= 0) {
      next.expectedNetContents = 'Enter a net-content value greater than 0.'
    }

    if (!image) next.image = 'Choose one label image or composite.'
    else if (image.size > MAX_IMAGE_BYTES) next.image = 'Choose an image no larger than 10 MB.'
    else if (image.type && !SUPPORTED_IMAGE_TYPES.has(image.type)) {
      next.image = 'Choose a JPEG, PNG, or WebP image.'
    }

    return next
  }

  function focusFirstError(next: FieldErrors) {
    const refs: Array<[FieldName, React.RefObject<HTMLInputElement | null>]> = [
      ['brandName', brandRef],
      ['classType', classTypeRef],
      ['expectedAbv', abvRef],
      ['expectedNetContents', netContentsRef],
      ['image', imageRef],
    ]
    refs.find(([field]) => next[field])?.[1].current?.focus()
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (isSubmitting) return

    const nextErrors = validate()
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0 || !image) {
      focusFirstError(nextErrors)
      return
    }

    void onSubmit({
      brandName: brandName.trim(),
      classType: classType.trim(),
      expectedAbv,
      expectedNetContents,
      expectedNetContentsUnit,
      image,
    })
  }

  return (
    <section className="panel form-panel" aria-labelledby="review-form-title">
      <div className="section-heading">
        <p className="step-label">Step 1</p>
        <h2 id="review-form-title">Enter application values</h2>
        <p>Provide the values the submitted label artwork is expected to show.</p>
      </div>

      <form noValidate onSubmit={handleSubmit} aria-busy={isSubmitting}>
        <div className="field-group">
          <label htmlFor="brand-name">Expected brand name</label>
          <input
            id="brand-name"
            ref={brandRef}
            name="brand-name"
            type="text"
            maxLength={200}
            autoComplete="off"
            value={brandName}
            aria-invalid={Boolean(errors.brandName)}
            aria-describedby={errors.brandName ? 'brand-name-error' : undefined}
            onChange={(event) => {
              setBrandName(event.target.value)
              setFieldError('brandName')
            }}
          />
          {errors.brandName && (
            <p className="field-error" id="brand-name-error">
              <span aria-hidden="true">!</span> {errors.brandName}
            </p>
          )}
        </div>

        <div className="field-group">
          <label htmlFor="class-type">Expected class or type</label>
          <input
            id="class-type"
            ref={classTypeRef}
            name="class-type"
            type="text"
            maxLength={200}
            autoComplete="off"
            value={classType}
            aria-invalid={Boolean(errors.classType)}
            aria-describedby={errors.classType ? 'class-type-error' : 'class-type-hint'}
            onChange={(event) => {
              setClassType(event.target.value)
              setFieldError('classType')
            }}
          />
          <p className="field-hint" id="class-type-hint">
            For example, Kentucky Straight Bourbon Whiskey.
          </p>
          {errors.classType && (
            <p className="field-error" id="class-type-error">
              <span aria-hidden="true">!</span> {errors.classType}
            </p>
          )}
        </div>

        <div className="field-row">
          <div className="field-group">
            <label htmlFor="expected-abv">Expected alcohol by volume</label>
            <div className="input-suffix">
              <input
                id="expected-abv"
                ref={abvRef}
                name="expected-abv"
                type="number"
                min="0"
                max="100"
                step="any"
                inputMode="decimal"
                value={expectedAbv}
                aria-invalid={Boolean(errors.expectedAbv)}
                aria-describedby={errors.expectedAbv ? 'expected-abv-error' : undefined}
                onChange={(event) => {
                  setExpectedAbv(event.target.value)
                  setFieldError('expectedAbv')
                }}
              />
              <span aria-hidden="true">% ABV</span>
            </div>
            {errors.expectedAbv && (
              <p className="field-error" id="expected-abv-error">
                <span aria-hidden="true">!</span> {errors.expectedAbv}
              </p>
            )}
          </div>

          <div className="field-group">
            <label htmlFor="expected-net-contents">Expected net contents</label>
            <div className="compound-input">
              <input
                id="expected-net-contents"
                ref={netContentsRef}
                name="expected-net-contents"
                type="number"
                min="0"
                step="any"
                inputMode="decimal"
                value={expectedNetContents}
                aria-invalid={Boolean(errors.expectedNetContents)}
                aria-describedby={
                  errors.expectedNetContents ? 'expected-net-contents-error' : undefined
                }
                onChange={(event) => {
                  setExpectedNetContents(event.target.value)
                  setFieldError('expectedNetContents')
                }}
              />
              <label className="sr-only" htmlFor="net-contents-unit">
                Net-contents unit
              </label>
              <select
                id="net-contents-unit"
                name="net-contents-unit"
                value={expectedNetContentsUnit}
                onChange={(event) =>
                  setExpectedNetContentsUnit(event.target.value as 'mL' | 'L')
                }
              >
                <option value="mL">mL</option>
                <option value="L">L</option>
              </select>
            </div>
            {errors.expectedNetContents && (
              <p className="field-error" id="expected-net-contents-error">
                <span aria-hidden="true">!</span> {errors.expectedNetContents}
              </p>
            )}
          </div>
        </div>

        <fieldset className="image-fieldset">
          <legend>Label image</legend>
          <p className="field-hint" id="image-guidance">
            Upload one JPEG, PNG, or WebP image up to 10 MB. Use a clear image at least 800
            pixels on its shortest side; a single composite may show multiple label panels.
          </p>
          <label className="file-picker" htmlFor="label-image">
            <span className="file-picker-icon" aria-hidden="true">
              ↑
            </span>
            <span>
              <strong>{image ? 'Replace label image' : 'Choose label image'}</strong>
              <small>{image ? `${image.name} · ${formatFileSize(image.size)}` : 'No file chosen'}</small>
            </span>
          </label>
          <input
            className="visually-hidden-file"
            id="label-image"
            ref={imageRef}
            name="label-image"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            aria-label="Label image"
            aria-invalid={Boolean(errors.image)}
            aria-describedby={errors.image ? 'image-guidance image-error' : 'image-guidance'}
            onChange={(event) => handleImageChange(event.target.files?.[0] ?? null)}
          />
          {errors.image && (
            <p className="field-error" id="image-error">
              <span aria-hidden="true">!</span> {errors.image}
            </p>
          )}

          {previewUrl && image && (
            <div className="image-preview">
              <img src={previewUrl} alt={`Preview of ${image.name}`} />
              <button
                className="text-button"
                type="button"
                onClick={() => {
                  handleImageChange(null)
                  if (imageRef.current) imageRef.current.value = ''
                }}
              >
                Remove image
              </button>
            </div>
          )}
        </fieldset>

        <div className="privacy-note">
          <span className="privacy-icon" aria-hidden="true">
            i
          </span>
          <p>
            <strong>Use synthetic or non-sensitive data.</strong> Uploaded images are held only
            while this review runs, then deleted. This prototype does not create a case record.
          </p>
        </div>

        <button className="primary-button" type="submit" disabled={isSubmitting}>
          {isSubmitting ? (
            <>
              <span className="spinner" aria-hidden="true" /> Reviewing label…
            </>
          ) : (
            'Review label'
          )}
        </button>
      </form>
    </section>
  )
}
