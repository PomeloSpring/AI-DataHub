"""OpenCV image tools — 图像分析/处理能力,作为 Agent 系统工具暴露.

所有函数接收附件行 dict(含 storage_path),返回可 JSON 序列化的结果;
处理产物(image_process / detect_table_region)另存为派生附件。
"""

import logging
import os

logger = logging.getLogger(__name__)


def _read_image(att: dict):
    """读取附件图片为 cv2 BGR 数组;失败返回 None."""
    import cv2
    import numpy as np

    path = att.get("storage_path", "")
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def image_info(att: dict) -> dict:
    """图像基础信息:尺寸/通道/亮度/对比度."""
    import cv2

    img = _read_image(att)
    if img is None:
        return {"error": f"无法读取图片: {att.get('filename', '')}"}
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return {
        "filename": att.get("filename", ""),
        "width": int(w),
        "height": int(h),
        "channels": int(img.shape[2]) if img.ndim == 3 else 1,
        "mean_brightness": round(float(gray.mean()), 2),
        "contrast_std": round(float(gray.std()), 2),
        "size_bytes": int(att.get("size", 0)),
    }


def summarize_image(att: dict) -> str:
    """生成图像文本摘要(Vision 不可用时的降级描述)."""
    import json
    info = image_info(att)
    if info.get("error"):
        return info["error"]
    return json.dumps(info, ensure_ascii=False)


def image_process(att: dict, operation: str, params: dict = None) -> dict:
    """图像处理:resize / crop / grayscale / edges,产物保存为派生附件.

    Args:
        operation: resize(需 width) | crop(需 x,y,width,height) | grayscale | edges
    """
    import cv2

    params = params or {}
    img = _read_image(att)
    if img is None:
        return {"error": f"无法读取图片: {att.get('filename', '')}"}

    if operation == "resize":
        width = int(params.get("width", 0))
        if width <= 0:
            return {"error": "resize 需要正整数 width 参数"}
        h, w = img.shape[:2]
        scale = width / w
        out = cv2.resize(img, (width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        out_name = f"resized_{att.get('filename', 'image.jpg')}"
    elif operation == "crop":
        try:
            x, y, cw, ch = int(params["x"]), int(params["y"]), int(params["width"]), int(params["height"])
        except (KeyError, ValueError, TypeError):
            return {"error": "crop 需要整数参数 x, y, width, height"}
        h, w = img.shape[:2]
        x, y = max(0, x), max(0, y)
        x2, y2 = min(w, x + cw), min(h, y + ch)
        if x2 <= x or y2 <= y:
            return {"error": "crop 区域超出图像范围"}
        out = img[y:y2, x:x2]
        out_name = f"cropped_{att.get('filename', 'image.jpg')}"
    elif operation == "grayscale":
        out = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        out_name = f"gray_{att.get('filename', 'image.jpg')}"
    elif operation == "edges":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        out = cv2.Canny(gray, 50, 150)
        out_name = f"edges_{att.get('filename', 'image.jpg')}"
    else:
        return {"error": f"不支持的操作: {operation}(可选: resize/crop/grayscale/edges)"}

    # 统一编码为 PNG 保存(兼容灰度图)
    ok, buf = cv2.imencode(".png", out)
    if not ok:
        return {"error": "图像编码失败"}
    out_name = os.path.splitext(out_name)[0] + ".png"

    from services.datamind.multimodal.loader import save_derived_attachment
    new_att = save_derived_attachment(att, buf.tobytes(), out_name, {"operation": operation, "params": params})
    oh, ow = out.shape[:2]
    return {
        "success": True,
        "operation": operation,
        "attachment_id": new_att["id"],
        "filename": out_name,
        "url": f"/api/chat/attachments/{new_att['id']}/file",
        "storage_path": new_att["storage_path"],
        "width": int(ow),
        "height": int(oh),
    }


def detect_table_region(att: dict) -> dict:
    """检测图像中的表格区域(最大四边形轮廓)并裁剪保存.

    用于图表截图/扫描表格的前处理;未检测到时返回提示。
    """
    import cv2
    import numpy as np

    img = _read_image(att)
    if img is None:
        return {"error": f"无法读取图片: {att.get('filename', '')}"}

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    # 膨胀连接断裂线段
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = img.shape[0] * img.shape[1]
    best = None
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        area = cv2.contourArea(c)
        if area < img_area * 0.05:
            break
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            best = approx
            break

    if best is None:
        # 未找到四边形:退化为最大轮廓的外接矩形
        if contours and cv2.contourArea(contours[0]) >= img_area * 0.1:
            x, y, w, h = cv2.boundingRect(contours[0])
            cropped = img[y:y + h, x:x + w]
            detected = {"method": "bounding_rect", "x": x, "y": y, "width": w, "height": h}
        else:
            return {"detected": False, "message": "未检测到明显表格区域,建议直接使用原图"}
    else:
        rect = cv2.minAreaRect(best)
        box = cv2.boxPoints(rect).astype(np.int32)
        x, y, w, h = cv2.boundingRect(box)
        cropped = img[y:y + h, x:x + w]
        detected = {"method": "quadrilateral", "x": x, "y": y, "width": w, "height": h}

    ok, buf = cv2.imencode(".png", cropped)
    if not ok:
        return {"error": "裁剪结果编码失败"}

    from services.datamind.multimodal.loader import save_derived_attachment
    out_name = f"table_region_{os.path.splitext(att.get('filename', 'image'))[0]}.png"
    new_att = save_derived_attachment(att, buf.tobytes(), out_name, {"region": detected})
    return {
        "detected": True,
        "region": detected,
        "attachment_id": new_att["id"],
        "filename": out_name,
        "url": f"/api/chat/attachments/{new_att['id']}/file",
        "storage_path": new_att["storage_path"],
    }
