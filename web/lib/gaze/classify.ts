/**
 * 시선 한 장면 나누기 (#177).
 * 얼굴 랜드마크와 눈 블렌드셰이프로 카메라 보기 / 화면 보기 / 화면 밖 / 얼굴 없음을 가릅니다.
 * 카메라 위치와 앉은 자세가 사람마다 달라서, 면접 전에 카메라를 본 3초를 기준(baseline)으로 삼고 그 차이만 봅니다.
 * 머리 방향은 각도 대신 코끝이 얼굴 가운데에서 얼마나 벗어났는지(얼굴 폭·높이에 대한 비율)로 잽니다.
 * 기준값은 실제 카메라로 맞춘 근삿값이라 결과는 참고용입니다.
 */

export type GazeZone = "camera" | "screen" | "away" | "none";

/** 한 장면의 특징값. 모두 얼굴 크기에 대한 비율이거나 0~1 블렌드셰이프 값입니다 */
export interface GazeFeatures {
  /** 코끝의 좌우 치우침 (얼굴 폭 대비). 고개를 돌리면 커집니다 */
  headX: number;
  /** 코끝의 위아래 치우침 (얼굴 높이 대비). 고개를 숙이거나 들면 바뀝니다 */
  headY: number;
  /** 눈동자 좌우 (eyeLookOut/In 조합). 부호는 기준과의 차이로만 씁니다 */
  eyeX: number;
  /** 눈동자 위(+)아래(-) */
  eyeY: number;
}

/** 카메라를 본 것으로 치는 범위 (기준과의 차이) */
const CAMERA = { headX: 0.06, headY: 0.05, eyeX: 0.25, eyeY: 0.2 };
/** 이보다 크게 벗어나면 화면 밖으로 칩니다 */
const AWAY = { headX: 0.2, headY: 0.15, eyeX: 0.6 };
/** 눈만 이만큼 위로 올리면 화면 밖(위쪽)으로 칩니다. 화면은 카메라 아래에 있어 위를 보면 화면도 카메라도 아닙니다 */
const EYE_UP_AWAY = 0.3;

/** 얼굴 랜드마크 번호 (MediaPipe Face Mesh 468점) */
const NOSE_TIP = 1;
const FOREHEAD = 10;
const CHIN = 152;
const LEFT_EDGE = 234;
const RIGHT_EDGE = 454;

interface Point {
  x: number;
  y: number;
}

interface Category {
  categoryName: string;
  score: number;
}

/** 랜드마크와 블렌드셰이프에서 특징값을 뽑습니다. 얼굴이 너무 작거나 점이 모자라면 null입니다 */
export function featuresOf(landmarks: Point[], blendshapes: Category[]): GazeFeatures | null {
  const nose = landmarks[NOSE_TIP];
  const top = landmarks[FOREHEAD];
  const chin = landmarks[CHIN];
  const left = landmarks[LEFT_EDGE];
  const right = landmarks[RIGHT_EDGE];
  if (!nose || !top || !chin || !left || !right) return null;
  const width = Math.abs(right.x - left.x);
  const height = Math.abs(chin.y - top.y);
  if (width < 0.02 || height < 0.02) return null;
  const score = new Map(blendshapes.map((c) => [c.categoryName, c.score]));
  const s = (name: string) => score.get(name) ?? 0;
  return {
    headX: (nose.x - (left.x + right.x) / 2) / width,
    headY: (nose.y - (top.y + chin.y) / 2) / height,
    // 왼눈 바깥 + 오른눈 안쪽 = 한쪽으로, 반대 조합 = 다른 쪽으로 봅니다.
    eyeX: (s("eyeLookOutLeft") + s("eyeLookInRight") - s("eyeLookInLeft") - s("eyeLookOutRight")) / 2,
    eyeY: (s("eyeLookUpLeft") + s("eyeLookUpRight") - s("eyeLookDownLeft") - s("eyeLookDownRight")) / 2,
  };
}

/** 보정 중 모은 장면들의 중앙값을 기준으로 씁니다. 잠깐 눈을 돌린 장면에 덜 흔들립니다 */
export function baselineOf(samples: GazeFeatures[]): GazeFeatures | null {
  if (samples.length === 0) return null;
  const median = (pick: (f: GazeFeatures) => number) => {
    const values = samples.map(pick).sort((a, b) => a - b);
    const mid = Math.floor(values.length / 2);
    return values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2;
  };
  return {
    headX: median((f) => f.headX),
    headY: median((f) => f.headY),
    eyeX: median((f) => f.eyeX),
    eyeY: median((f) => f.eyeY),
  };
}

/** 기준과 비교해 한 장면이 어디를 봤는지 나눕니다 */
export function classify(features: GazeFeatures | null, baseline: GazeFeatures): GazeZone {
  if (!features) return "none";
  const headX = Math.abs(features.headX - baseline.headX);
  const headY = Math.abs(features.headY - baseline.headY);
  const eyeX = Math.abs(features.eyeX - baseline.eyeX);
  const eyeUp = features.eyeY - baseline.eyeY;
  if (headX > AWAY.headX || headY > AWAY.headY || eyeX > AWAY.eyeX || eyeUp > EYE_UP_AWAY) return "away";
  if (headX < CAMERA.headX && headY < CAMERA.headY && eyeX < CAMERA.eyeX && Math.abs(eyeUp) < CAMERA.eyeY) {
    return "camera";
  }
  return "screen";
}
