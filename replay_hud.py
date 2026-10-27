"""HUD für gespeicherte Rohbilder ohne erneute Fahrspurerkennung."""
import json
import math
import cv2


def encode_lanes(lanes):
    """Übernimmt ausschließlich numerische Linienkoeffizienten aus dem Datenpaket."""
    result=[]
    for lane in lanes or ():
        if lane is None:
            result.append(None)
            continue
        try:
            values=list(lane)
            if len(values)!=2 or not all(math.isfinite(float(x)) for x in values):
                result.append(None)
            else:
                result.append([float(x) for x in values])
        except (TypeError, ValueError, OverflowError):
            result.append(None)
    return json.dumps(result)


def draw_hud(image, row):
    """Zeigt gespeicherte Linien und Winkel; keine neuen Messwerte werden berechnet."""
    if image is None or getattr(image,'size',0)==0:
        return image
    result=image.copy()
    height,width=result.shape[:2]
    def number(key):
        try:
            v=float(row.get(key,''))
            return v if math.isfinite(v) else None
        except (ValueError,TypeError):return None
    try:lanes=json.loads(row.get('lanes_json') or '[]')
    except (ValueError,TypeError):lanes=[]
    colors=[(50,230,100),(20,200,255)]
    endpoints=[]
    upper_points=[]
    for i,lane in enumerate(lanes[:2]):
        if not isinstance(lane,list) or len(lane)!=2:continue
        try:
            m,b=map(float,lane)
            if not all(map(math.isfinite,(m,b))):continue
            # Vorhandenes Linienmodell: x = m*y + b.
            y0,y1=int(height*.48),height-1
            x0=int(max(-width*2,min(width*3,m*y0+b)))
            x1=int(max(-width*2,min(width*3,m*y1+b)))
            cv2.line(result,(x0,y0),(x1,y1),colors[i],3,cv2.LINE_AA)
            endpoints.append(x1)
            upper_points.append(x0)
        except (ValueError,TypeError,OverflowError):continue
    angle=number('steering_angle_car')
    if angle is None:angle=number('steering_angle')
    # Lenkrichtung ist eine Visualisierung des Stellwerts, keine Vorhersage.
    if angle is not None:
        origin=(int(sum(endpoints)/2),height-1) if len(endpoints)==2 else (width//2,height-1)
        theta=math.radians(max(-70,min(70,angle-90)))
        length=int(height*.42)
        target=(int(origin[0]+length*math.sin(theta)),int(origin[1]-length*math.cos(theta)))
        cv2.arrowedLine(result,origin,target,(255,210,40),3,cv2.LINE_AA,tipLength=.13)
    shade=result.copy()
    cv2.rectangle(shade,(0,0),(width,min(height,76)),(12,20,32),-1)
    cv2.addWeighted(shade,.68,result,.32,0,result)
    def text(value,x,y,scale=.5):
        cv2.putText(result,str(value),(x,y),cv2.FONT_HERSHEY_SIMPLEX,scale,(245,245,245),1,cv2.LINE_AA)
    text('FRAME '+str(row.get('frame_id','?')),12,23,.65)
    text('STEER '+(f'{angle:.0f} deg' if angle is not None else 'N/A'),12,46)
    text('SPEED '+str(row.get('speed') or 'N/A'),min(width//2,210),46)
    text(str(row.get('data_status') or 'unknown').upper(),12,67,.4)
    if row.get('event_codes'):
        cv2.rectangle(result,(0,height-32),(width,height),(15,30,100),-1)
        text('EVENT: '+str(row['event_codes'])[:55],10,height-10,.45)
    return result
